"""Running model-written Python without letting it take the server with it.

### FORKED CODE

`run(code)` makes a temp directory with an empty `out/` inside, writes the code
and the input paths to a job file, and starts

    python -m external.sandbox_runner job.json

as a separate process — cwd inside the temp directory so the code cannot see the
repo, environment built from an allowlist so it holds no credentials. It waits
with a wall-clock timeout, killing the whole process group if that expires, then
reads whatever files ended up in `out/` and throws the directory away.

Files are the entire protocol. A `.parquet` is a frame, a `.json` a chart, a
`.md` a report, anything else raw bytes — so a matplotlib PNG or a pickle works
without this file knowing they exist.

The separate process is there for the timeout. Code that loops forever cannot be
interrupted in a thread, and `exec()` in this process would hand generated code
the database pool and every credential in the environment. It raises the cost of
carelessness; it is not a boundary against a determined adversary. That is the
container's job.

`ExecutionResult.check()` is here rather than in a service of its own because
judging whether an output is degenerate means knowing how outputs are shaped,
and that is this file.
"""

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from external.base import ExternalService
from settings import settings
from logger import logger
from exceptions.exceptions import ExternalServiceException

#: Generated code prints for its own benefit; the model only needs enough to
#: debug. Anything past this is truncated with a marker rather than dropped.
MAX_STREAM_CHARS = 4_000

#: A chart needs at least this many points to say anything. One point is not a
#: trend, it is a coincidence.
MIN_CHART_POINTS = 2

#: What a suffix means. This is the whole type system.
KINDS = {".parquet": "frame", ".json": "chart", ".md": "report", ".txt": "report"}


@dataclass
class Output:
    """One file the code produced, read into memory before its directory goes.

    A DataFrame, a dict, a str or raw bytes, according to `kind`.
    """

    kind: str
    value: Any
    #: The extension it was written with. For a `file` this is the only record
    #: of what it is, so it travels with the bytes.
    suffix: str = ""


@dataclass
class ExecutionResult:
    """What came back. Never raises for user-code failure — that is data."""

    ok: bool
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    error_type: Optional[str] = None
    outputs: dict[str, Output] = field(default_factory=dict)
    timed_out: bool = False

    def as_feedback(self) -> str:
        """The failure, shaped for a model to act on rather than a human to read."""
        if self.ok:
            return ""
        if self.timed_out:
            return (
                "Execution timed out. The code likely loops forever or scans far "
                "more data than needed. Reduce the work and try again."
            )
        head = (
            f"{self.error_type}: {self.error}"
            if self.error_type
            else (self.error or "failed")
        )
        return f"{head}\n\nstderr:\n{self.stderr}" if self.stderr else head

    def check(self, expected: Iterable[str] = ()) -> Optional[str]:
        """What is wrong with this run, or None if nothing is.

        The cheap gate. It cannot judge whether an analysis answered the question
        — that needs a model — but it catches what needs no judgement: the code
        raised, produced nothing, produced the wrong thing, or produced something
        technically valid and plainly useless like a one-point chart.

        `expected` is a list of names, not a task: this file knows about files,
        not about plans.
        """
        if not self.ok:
            return self.error or "execution failed with no error reported"

        wanted = list(expected)
        missing = [name for name in wanted if name not in self.outputs]
        if missing:
            return (
                f"declared {sorted(missing)} but produced "
                f"{sorted(self.outputs) or 'nothing'}. Write each one into `out`."
            )

        for name in wanted:
            kind, value = self.outputs[name].kind, self.outputs[name].value

            if kind == "frame":
                if value is None or len(value) == 0:
                    return (
                        f"{name!r} has no rows — the filter or join likely excluded "
                        f"everything. Check the join keys and any date bounds."
                    )
                if len(value.columns) == 0:
                    return f"{name!r} has no columns"
                # All-null is technically a result and never a useful one.
                if value.notna().to_numpy().sum() == 0:
                    return (
                        f"{name!r} contains only nulls — the source column is likely "
                        f"empty or the join did not match."
                    )

            elif kind == "chart":
                if not isinstance(value, dict) or not value:
                    return f"{name!r} is not a chart specification"
                # Presence, not truthiness: an empty list is exactly the
                # degenerate case, and `or` would skip straight past it.
                series = next(
                    (value[k] for k in ("data", "series", "values") if k in value), None
                )
                if isinstance(series, list):
                    if len(series) < MIN_CHART_POINTS:
                        return (
                            f"{name!r} has {len(series)} data point(s); a chart needs "
                            f"at least {MIN_CHART_POINTS} to show anything."
                        )
                    if len({str(p) for p in series}) == 1:
                        return (
                            f"{name!r} is a single repeated value — the grouping key "
                            f"probably collapsed to one category."
                        )

            elif not value or (kind == "report" and not str(value).strip()):
                return f"{name!r} is empty"

        return None


def _truncate(text: str, limit: int = MAX_STREAM_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} more characters]"


def _child_env(scratch: Path) -> dict[str, str]:
    """A minimal environment for the child.

    An allowlist, so a new credential variable is excluded by default rather than
    by remembering to exclude it. The passthroughs are what native extensions
    need — pyarrow fails to load without them, with an error naming neither
    pyarrow nor the environment.

    PYTHONPATH is set absolute: the child's cwd is the scratch directory, so a
    relative entry inherited from the parent (a plain "." is common) would
    resolve there and the runner would not import.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
    }
    for key in (
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
    ):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _read_outputs(out: Path, cap: int) -> dict[str, Output]:
    """Every file the code left behind. The name is the filename without its
    suffix, so `out/cohorts.parquet` comes back as `cohorts`."""
    import pandas as pd

    files = [p for p in sorted(out.iterdir()) if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    if total > cap:
        raise ValueError(
            f"outputs total {total} bytes, over the {cap} limit. "
            f"Aggregate before writing."
        )

    outputs = {}
    for path in files:
        kind = KINDS.get(path.suffix, "file")
        if kind == "frame":
            value = pd.read_parquet(path)
        elif kind == "chart":
            value = json.loads(path.read_text())
        elif kind == "report":
            value = path.read_text()
        else:
            value = path.read_bytes()
        outputs[path.stem] = Output(kind=kind, value=value, suffix=path.suffix)
    return outputs


class SandboxService(ExternalService):
    """Registered like every other external dependency."""

    def __init__(self):
        self.timeout_s = settings.SANDBOX_TIMEOUT_S
        self.max_output_bytes = settings.MAX_OUTPUT_BYTES

    async def startup(self) -> None:
        logger.info(
            f"Sandbox ready (timeout={self.timeout_s}s, "
            f"max output={self.max_output_bytes} bytes)"
        )

    async def sanity_check(self) -> None:
        result = await self.run("emit('probe', {'ok': True})")
        if not result.ok:
            raise ExternalServiceException(
                f"sandbox probe failed: {result.error}", "SandboxService"
            )
        logger.info("Sandbox sanity check passed")

    async def shutdown(self) -> None:
        pass

    async def run(
        self,
        code: str,
        inputs: Optional[Mapping[str, Path]] = None,
        timeout_s: Optional[float] = None,
    ) -> ExecutionResult:
        timeout_s = timeout_s or self.timeout_s

        with tempfile.TemporaryDirectory(prefix="agentics-sandbox-") as tmp:
            scratch = Path(tmp)
            out = scratch / "out"
            out.mkdir()
            result_path = scratch / "result.json"
            job = scratch / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "code": code,
                        "inputs": {k: str(v) for k, v in (inputs or {}).items()},
                        "out": str(out),
                        "result_path": str(result_path),
                    }
                )
            )

            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "external.sandbox_runner",
                str(job),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(scratch),  # generated code cannot see the repo
                preexec_fn=os.setsid,  # own group, so the timeout kills descendants
                env=_child_env(scratch),
            )

            try:
                raw_out, raw_err = await asyncio.wait_for(
                    process.communicate(), timeout=timeout_s
                )
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(process.pid), 9)
                except (ProcessLookupError, PermissionError):
                    process.kill()
                await process.wait()
                return ExecutionResult(
                    ok=False,
                    timed_out=True,
                    error_type="Timeout",
                    error=f"execution exceeded {timeout_s}s",
                )

            stdout = _truncate(raw_out.decode("utf-8", "replace"))
            stderr = _truncate(raw_err.decode("utf-8", "replace"))

            if not result_path.exists():
                # Died before reporting — an OOM kill from the container, or a
                # segfault. stderr is the only evidence.
                return ExecutionResult(
                    ok=False,
                    stdout=stdout,
                    stderr=stderr,
                    error_type="SandboxCrash",
                    error=f"process exited with code {process.returncode} without reporting",
                )

            report = json.loads(result_path.read_text())
            try:
                outputs = _read_outputs(out, self.max_output_bytes)
            except ValueError as e:
                return ExecutionResult(
                    ok=False,
                    stdout=stdout,
                    stderr=stderr,
                    error=str(e),
                    error_type="OutputTooLarge",
                )

            return ExecutionResult(
                ok=bool(report.get("ok")),
                stdout=stdout,
                stderr=stderr,
                error=report.get("error"),
                error_type=report.get("error_type"),
                outputs=outputs,
            )


sandbox_service = SandboxService()
