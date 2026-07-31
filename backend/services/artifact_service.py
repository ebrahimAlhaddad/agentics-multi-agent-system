"""The bridge between object storage and the database.

An artifact is two things: bytes somewhere, and a row pointing at them. This is
the only place that knows that, and the only place that knows how each kind is
encoded. Callers hand over a dataframe, a chart spec or some text and get back a
row

Centralising it is what stops every caller from re-deriving profiles and row
counts slightly differently.
"""

import io
import json
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Sequence
from uuid import UUID, uuid4

import pandas as pd
from exceptions.exceptions import NotFoundException, ServiceLayerException
from external.postgres import postgres_service
from external.storage import storage_service
from logger import logger
from models.artifact import ArtifactHandle, ArtifactKind, ArtifactOrigin
from models.db.artifact import Artifact, ArtifactProfile
from models.db.run import Run
from settings import settings

from services.profile_service import profile_service

#: How many rows of a frame a summary shows. Enough to spot a wrong grouping or
#: a nonsense magnitude, not enough to become the data itself.
SAMPLE_ROWS = 5
#: How much of a written artifact a summary quotes.
MAX_SUMMARY_CHARS = 2_000


class ArtifactService:
    def __init__(self):
        self.db = postgres_service
        self.storage = storage_service

    # ------------------------------------------------------------- handles

    #: Handle construction lives in constants.ArtifactHandle so that pure
    #: services can build them too
    handle = staticmethod(ArtifactHandle.build)
    parse_handle = staticmethod(ArtifactHandle.parse)

    # ------------------------------------------------------------- encoding

    @staticmethod
    def _encode(kind: str, obj: Any) -> bytes:
        """Object -> bytes for the given kind. The inverse of `decode`."""
        if kind == ArtifactKind.FRAME:
            if not isinstance(obj, pd.DataFrame):
                raise ValueError(
                    f"{ArtifactKind.FRAME} artifacts need a DataFrame, got {type(obj).__name__}"
                )
            buf = io.BytesIO()
            obj.to_parquet(buf, index=False)
            return buf.getvalue()

        if kind == ArtifactKind.CHART:
            if not isinstance(obj, dict):
                raise ValueError(
                    f"{ArtifactKind.CHART} artifacts need a dict, got {type(obj).__name__}"
                )
            return json.dumps(obj).encode("utf-8")

        if kind == ArtifactKind.REPORT:
            if not isinstance(obj, str):
                raise ValueError(
                    f"{ArtifactKind.REPORT} artifacts need a str, got {type(obj).__name__}"
                )
            return obj.encode("utf-8")

        if kind == ArtifactKind.FILE:
            if not isinstance(obj, bytes):
                raise ValueError(
                    f"{ArtifactKind.FILE} artifacts need bytes, got {type(obj).__name__}"
                )
            return obj

        raise ValueError(
            f"unknown artifact kind {kind!r}, expected one of {sorted(ArtifactKind.ALL)}"
        )

    @staticmethod
    def decode(kind: str, data: bytes) -> Any:
        if kind == ArtifactKind.FRAME:
            return pd.read_parquet(io.BytesIO(data))
        if kind == ArtifactKind.CHART:
            return json.loads(data.decode("utf-8"))
        if kind == ArtifactKind.REPORT:
            return data.decode("utf-8")
        if kind == ArtifactKind.FILE:
            return data
        raise ValueError(f"unknown artifact kind {kind!r}")

    # --------------------------------------------------------------- write

    async def _session_of(self, run_id: UUID) -> UUID:
        """The session a run belongs to, for keying its artifacts."""
        run = await self.db.get(Run, run_id)
        if run is None:
            raise NotFoundException(f"run {run_id} not found")
        return run.session_id

    async def _write(
        self,
        session_id: UUID,
        run_id: Optional[UUID],
        task_id: Optional[str],
        name: str,
        kind: str,
        obj: Any,
        origin: str,
        suffix: Optional[str] = None,
    ) -> Artifact:
        """The one write path. Uploads and task outputs both land here.

        **Writing the same name twice replaces it.** An agent that produces a
        result, notices it is wrong and produces it again is doing the right
        thing, and so is a retried task;

        Bytes are written first
        """
        data = self._encode(kind, obj)
        extension = ArtifactKind.EXTENSIONS.get(kind) or (suffix or "").lstrip(".")
        if not extension:
            raise ValueError(f"{kind} artifacts need a suffix, e.g. '.png'")
        # if artifact already exists by name, fetch its id to replace
        existing = await self._named(session_id, run_id, name)
        artifact_id = existing.artifact_id if existing else uuid4()
        # the key reflects the handle but separate for storage: session_45434/artifact_34343.parquet
        key = self.storage.key(str(session_id), f"{artifact_id}.{extension}")
        await self.storage.put(key, data)

        if existing:
            # A rewrite may change kind, and with it the extension and the key.
            # Drop the object the old row pointed at or it is orphaned.
            if existing.object_key != key:
                await self.storage.delete(existing.object_key)
            saved = await self.db.update(
                Artifact,
                existing,
                {"task_id": task_id, "kind": kind, "object_key": key},
            )
            await self.db.delete_list(ArtifactProfile, {"artifact_id": artifact_id})
        else:
            saved = await self.db.add(
                Artifact(
                    artifact_id=artifact_id,
                    session_id=session_id,
                    run_id=run_id,
                    task_id=task_id,
                    name=name,
                    origin=origin,
                    kind=kind,
                    object_key=key,
                )
            )

        # One profile shape
        if kind == ArtifactKind.FRAME:
            profile = profile_service.profile(obj)
            await self.db.add(
                ArtifactProfile(
                    artifact_id=artifact_id,
                    row_count=profile.get("row_count"),
                    columns=profile.get("columns"),
                )
            )

        logger.info(
            f"{'Replaced' if existing else 'Stored'} {kind} artifact {name!r} "
            f"in session {session_id} ({len(data)} bytes)"
        )
        return saved

    async def put(
        self,
        name: str,
        kind: str,
        obj: Any,
        *,
        run_id: Optional[UUID] = None,
        task_id: Optional[str] = None,
        session_id: Optional[UUID] = None,
        suffix: Optional[str] = None,
        origin: str = ArtifactOrigin.TRANSIENT,
    ) -> Artifact:
        """Store something as an artifact. The one public write.

        Takes whichever scope the caller has, the same way `resolve` does: a
        run knows its session, so a task passes `run_id` and a `task_id`, while
        an upload passes `session_id` and neither.

        `task_id` qualifies the name — `totals` written by `n_mrr` is stored as
        `n_mrr/totals` — which is what lets two tasks produce the same local
        name without colliding, and what makes a re-plan's names distinct from
        the previous plan's

        `origin` is TERMINAL for the run's answer, so anything asking "what did
        this run conclude" finds it without guessing from names.
        """
        if session_id is None:
            if run_id is None:
                raise ValueError("put needs a run_id or a session_id")
            session_id = await self._session_of(run_id)

        try:
            return await self._write(
                session_id=session_id,
                run_id=run_id,
                task_id=task_id,
                name=f"{task_id}/{name}" if task_id else name,
                kind=kind,
                obj=obj,
                origin=origin,
                suffix=suffix,
            )
        except ValueError:
            raise
        except Exception as e:
            msg = f"Error storing artifact {name!r}: {e}"
            logger.error(msg)
            raise ServiceLayerException(msg, "ArtifactService") from e

    async def process_upload(
        self, session_id: UUID, filename: str, content: bytes
    ) -> Artifact:
        """Validate an uploaded CSV and store it as an artifact nobody produced.

        Stored as parquet rather than as the CSV that arrived: every reader
        expects the kind's encoding, and a frame's encoding is parquet. Keeping
        the original would make inputs the one artifact needing a special case.
        """
        if not content:
            raise ServiceLayerException("uploaded file is empty", "ArtifactService")
        if len(content) > settings.MAX_UPLOAD_BYTES:
            raise ServiceLayerException(
                f"file is {len(content)} bytes, over the "
                f"{settings.MAX_UPLOAD_BYTES} limit",
                "ArtifactService",
            )

        try:
            frame = pd.read_csv(io.BytesIO(content))
        except Exception as e:
            # A parse failure is the user's problem, not a server error
            raise ServiceLayerException(
                f"could not parse CSV: {e}", "ArtifactService"
            ) from e

        if frame.empty and not len(frame.columns):
            raise ServiceLayerException("CSV has no columns", "ArtifactService")

        stem = PurePosixPath(filename).stem or "upload"
        name = f"{ArtifactOrigin.INPUT_PREFIX}/{stem}"
        if await self.resolve(session_id=session_id, name=name, required=False):
            raise ServiceLayerException(
                f"this session already has an input named {stem!r}; "
                f"rename the file or start a new session",
                "ArtifactService",
            )

        return await self.put(
            name,
            ArtifactKind.FRAME,
            frame,
            session_id=session_id,
            origin=ArtifactOrigin.INPUT,
        )

    # ---------------------------------------------------------------- read

    async def _named(
        self, session_id: UUID, run_id: Optional[UUID], name: str
    ) -> Optional[Artifact]:
        """The one artifact with this name in this scope, or None."""
        rows = await self.db.get_list(
            Artifact, filters={"session_id": session_id, "name": name}
        )
        return next((a for a in rows if a.run_id == run_id), None)

    async def resolve(
        self,
        *,
        artifact_id: Optional[UUID] = None,
        run_id: Optional[UUID] = None,
        session_id: Optional[UUID] = None,
        name: Optional[str] = None,
        required: bool = True,
    ) -> Optional[Artifact]:
        """The one artifact a caller means, from whatever it happens to hold.

        An artifact is addressable three ways — by id, by name within a run, or
        by name within a session — and callers rarely hold the same pair. Rather
        than a method per combination, this takes what you have and derives the
        rest

        Resolving a name inside a run looks in three places, narrowest first:
        the run's own artifacts, then the session's uploads (which belong to no
        run), then a bare local name against the qualified ones.

        `session_id` is also the authorisation check: an artifact belongs to a
        session and a session belongs to a user, so a mismatch is NotFound
        rather than Forbidden

        `required=False` returns None instead of raising, for callers asking
        whether something is there.
        """
        if artifact_id is not None:
            found = await self.db.get(Artifact, artifact_id)
            if found is not None and session_id is not None:
                found = found if found.session_id == session_id else None
            missing = f"artifact {artifact_id} not found"

        elif name is not None:
            if session_id is None:
                if run_id is None:
                    raise ValueError("resolving by name needs a run_id or session_id")
                session_id = await self._session_of(run_id)

            found = await self._named(session_id, run_id, name)
            if found is None and run_id is not None:
                found = await self._named(session_id, None, name)
            if found is None and run_id is not None:
                candidates = [
                    a
                    for a in await self.list_for(run_id=run_id)
                    if a.name.rsplit("/", 1)[-1] == name
                ]
                if len(candidates) > 1:
                    # Two tasks produced this local name. Qualification exists
                    # so both can; a consumer naming it bare has not said which.
                    raise NotFoundException(
                        f"{name!r} is ambiguous for run {run_id}: produced by "
                        f"{sorted(a.task_id for a in candidates)}"
                    )
                found = candidates[0] if candidates else None
            missing = f"artifact {name!r} not found for run {run_id}"

        else:
            raise ValueError("resolve needs an artifact_id or a name")

        if found is None and required:
            raise NotFoundException(missing)
        return found

    async def read(self, artifact: Optional[Artifact] = None, **locator) -> Any:
        """The value behind an artifact, decoded by its recorded kind."""
        if artifact is None:
            artifact = await self.resolve(**locator)
        return self.decode(artifact.kind, await self.storage.get(artifact.object_key))

    async def read_handle(self, handle: str) -> Any:
        """A handle is just a locator in string form."""
        run_id, name = self.parse_handle(handle)
        return await self.read(run_id=UUID(run_id), name=name)

    async def profile(self, artifact_id: UUID) -> Optional[ArtifactProfile]:
        """What is inside it, or None if it was never profiled."""
        return await self.db.get(ArtifactProfile, artifact_id)

    async def list_for(self, **filters) -> list[Artifact]:
        """Artifacts matching whatever you filter on — run_id, session_id, kind,
        origin, any column. One method rather than one per question."""
        return await self.db.get_list(Artifact, filters=filters)

    async def delete(self, **filters) -> int:
        """Delete artifacts and their objects. Returns how many went.

        An `artifact_id` means one artifact, and `session_id` alongside it is
        the same authorisation check `resolve` makes. Any other filter means
        everything matching: `delete(run_id=…)` throws away what a run produced
        when its plan is rewritten, `delete(session_id=…)` empties a session.

        Objects go before rows, for the reason `_write` writes them first: an
        orphaned object is garbage, while a row pointing at bytes that are gone
        is a broken read
        """
        if filters.get("artifact_id") is not None:
            artifact = await self.resolve(
                artifact_id=filters["artifact_id"],
                session_id=filters.get("session_id"),
            )
            await self.storage.delete(artifact.object_key)
            await self.db.delete_list(Artifact, {"artifact_id": artifact.artifact_id})
            logger.info(
                f"Deleted artifact {artifact.name!r} from session {artifact.session_id}"
            )
            return 1

        rows = await self.list_for(**filters)
        if not rows:
            return 0
        if set(filters) == {"session_id"}:
            await self.storage.delete_prefix(str(filters["session_id"]))
        else:
            for artifact in rows:
                await self.storage.delete(artifact.object_key)
        await self.db.delete_list(Artifact, filters)
        logger.info(f"Deleted {len(rows)} artifact(s) matching {filters}")
        return len(rows)

    async def describe(
        self,
        names: Sequence[str],
        *,
        run_id: Optional[UUID] = None,
        session_id: Optional[UUID] = None,
    ) -> str:
        """Named artifacts and their columns, read from the profile.

        The catalog, not the data: `summarise` decodes every artifact to sample
        rows from it, which is what a report writer needs and far more than a
        reader asking "does this column exist" — the answer to that is already
        in artifact_profiles, and getting it costs one row instead of a whole
        parquet.

        A name that resolves to nothing is skipped rather than raised on. This
        answers "what is available", and a stale entry in a run's inputs is
        exactly the thing the caller wants left out of the list.
        """
        lines = []
        for name in names:
            artifact = await self.resolve(
                name=name, run_id=run_id, session_id=session_id, required=False
            )
            if artifact is None:
                continue
            profile = await self.profile(artifact.artifact_id)
            columns = [c.get("name") for c in (profile.columns or [])] if profile else []
            lines.append(
                f"- {name}: {', '.join(str(c) for c in columns) or 'unknown'}"
            )
        return "\n".join(lines) or "  none"

    async def summarise(self, run_id: UUID, names: Sequence[str]) -> str:
        """Named artifacts described rather than included.

        The counterpart to `stage`: staging hands an agent the bytes, this hands
        one a description..
        """
        if not names:
            return "  (nothing was produced)"

        lines = []
        for name in names:
            artifact = await self.resolve(run_id=run_id, name=name)
            value = await self.read(artifact)

            if artifact.kind == ArtifactKind.FRAME:
                profile = await self.profile(artifact.artifact_id)
                rows = profile.row_count if profile else len(value)
                lines.append(
                    f"- {name} (table, {rows} rows x {len(value.columns)} columns)\n"
                    f"  columns: {', '.join(str(c) for c in value.columns)}\n"
                    f"  first {SAMPLE_ROWS} rows:\n"
                    f"{value.head(SAMPLE_ROWS).to_string(index=False)}"
                )
            elif artifact.kind == ArtifactKind.CHART:
                lines.append(f"- {name} (chart specification)\n  {value}")
            elif artifact.kind == ArtifactKind.REPORT:
                lines.append(f"- {name} (text)\n  {value[:MAX_SUMMARY_CHARS]}")
            else:
                lines.append(
                    f"- {name} (file, {len(value)} bytes) — cannot be read as text"
                )
        return "\n".join(lines)

    # -------------------------------------------------------------- staging

    async def stage(
        self, run_id: UUID, names: Sequence[str], directory: Path
    ) -> dict[str, Path]:
        """Materialise artifacts as files a sandbox can read."""
        staged: dict[str, Path] = {}
        for name in names or []:
            artifact = await self.resolve(run_id=run_id, name=name)
            # The local half of the name: 'input/sales' stages as 'sales'.
            local = name.rsplit("/", 1)[-1]
            suffix = Path(artifact.object_key).suffix
            path = directory / f"{local}{suffix}"
            path.write_bytes(await self.storage.get(artifact.object_key))
            staged[local] = path
        return staged


artifact_service = ArtifactService()
