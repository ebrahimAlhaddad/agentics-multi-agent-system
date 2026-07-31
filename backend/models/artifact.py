"""Everything an artifact is, apart from its table.

Vocabulary, wire shapes and the handle format live together because they
describe one concept and change together. The SQLAlchemy model is the one piece
kept apart. pure services build handles and read kinds without pulling in the database.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ArtifactKind:
    #: A tabular result, encoded as parquet.
    FRAME = "frame"
    #: A chart specification, encoded as JSON. Stored as a spec rather than an
    #: image so it stays small and can be re-rendered.
    CHART = "chart"
    #: Narrative text, encoded as UTF-8.
    REPORT = "report"
    #: Anything else a task wrote — a PNG, a PDF, a pickled model
    FILE = "file"

    ALL = frozenset({FRAME, CHART, REPORT, FILE})

    #: File extension per kind, used when building the storage key. FILE is
    #: absent on purpose: it keeps whatever extension it was written with.
    EXTENSIONS = {FRAME: "parquet", CHART: "json", REPORT: "md"}


class ArtifactOrigin:
    """Where an artifact came from, and therefore how long it must survive."""

    #: Uploaded by a person. Belongs to the session, outlives every run.
    INPUT = "input"
    #: Written by a task while a run executed. Sweepable once the run ends.
    TRANSIENT = "transient"
    #: The run's answer, written by a terminal worker. Kept.
    TERMINAL = "terminal"

    ALL = frozenset({INPUT, TRANSIENT, TERMINAL})

    #: The prefix an upload's name is qualified with, standing where a producing
    #: task id would be. `input/sales` reads the same way as `n_cohorts/cohorts`.
    INPUT_PREFIX = "input"


class ArtifactHandle:
    """The opaque reference agents receive in place of artifact data."""

    SCHEME = "artifact"

    @staticmethod
    def build(run_id, name: str) -> str:
        return f"{ArtifactHandle.SCHEME}://{run_id}/{name}"

    @staticmethod
    def parse(handle: str) -> tuple[str, str]:
        prefix = f"{ArtifactHandle.SCHEME}://"
        if not isinstance(handle, str) or not handle.startswith(prefix):
            raise ValueError(f"not an artifact handle: {handle!r}")
        rest = handle[len(prefix) :]
        # form of handle — 'artifact://<run>/n_cohorts/cohorts'.
        run_id, sep, name = rest.partition("/")
        if not sep or not run_id or not name:
            raise ValueError(f"not an artifact handle: {handle!r}")
        return run_id, name

    @staticmethod
    def is_handle(value) -> bool:
        try:
            ArtifactHandle.parse(value)
            return True
        except ValueError:
            return False


class RouterArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artifact_id: UUID
    session_id: UUID
    #: Absent on an upload — nobody produced it.
    run_id: Optional[UUID] = None
    task_id: Optional[str] = None

    name: str
    origin: str
    kind: str

    row_count: Optional[int] = None
    columns: Optional[list[dict[str, Any]]] = None
    created_at: Optional[datetime] = None


class ArtifactSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artifact_id: UUID
    name: str
    origin: str
    kind: str
    created_at: Optional[datetime] = None
