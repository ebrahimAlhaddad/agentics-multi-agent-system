"""The child process. Runs one piece of model-written Python.

### FORKED CODE

Started as `python -m external.sandbox_runner <job.json>` by sandbox.py,
which is the only thing that talks to it, and only through files: it reads a job
spec and writes a result spec, with stdout and stderr captured by the parent.

The generated code gets three names:

    out           a directory. Any file written here is an output.
    load(name)    an input, read back as the object it was written as.
    emit(name, x) sugar — writes x into `out` for you.

It needs no database, no network and no credentials: everything it may read
arrives as a file path in the job spec.
"""

import json
import sys
import traceback
from pathlib import Path


def main(job_path: str) -> int:
    import pandas as pd

    job = json.loads(Path(job_path).read_text())
    out = Path(job["out"])
    inputs = {name: Path(p) for name, p in job.get("inputs", {}).items()}

    def load(name: str):
        """An input, as the object it was written as. The suffix is the only
        type information in here; there is no database to ask."""
        if name not in inputs:
            raise KeyError(
                f"{name!r} is not an input of this task; available: {sorted(inputs)}"
            )
        path = inputs[name]
        if path.suffix == ".parquet":
            return pd.read_parquet(path)
        if path.suffix == ".json":
            return json.loads(path.read_text())
        return path.read_text()

    def emit(name: str, obj) -> None:
        """Write an output without picking a file format. Only a convenience —
        `frame.to_parquet(out / 'x.parquet')` does the same thing."""
        if isinstance(obj, pd.DataFrame):
            obj.to_parquet(out / f"{name}.parquet", index=False)
        elif isinstance(obj, (dict, list)):
            (out / f"{name}.json").write_text(json.dumps(obj))
        elif isinstance(obj, str):
            (out / f"{name}.md").write_text(obj)
        elif isinstance(obj, bytes):
            (out / f"{name}.bin").write_bytes(obj)
        else:
            raise TypeError(
                f"emit({name!r}) does not handle {type(obj).__name__} — write it "
                f"to `out` yourself, e.g. out / '{name}.png'"
            )

    result = {"ok": False, "error": None, "error_type": None}
    try:
        exec(
            compile(job["code"], "<generated>", "exec"),
            {
                "__name__": "__sandbox__",
                "pd": pd,
                "out": out,
                "load": load,
                "emit": emit,
                "inputs": sorted(inputs),
            },
        )
        result["ok"] = True
    except Exception as e:
        # Trimmed to the generated frames: the model does not need this file's
        # internals, and a wall of runner frames crowds out what it can act on.
        frames = traceback.format_exception(type(e), e, e.__traceback__)
        result["error"] = "".join(
            f for f in frames if "sandbox_runner" not in f
        ).strip()
        result["error_type"] = type(e).__name__
    finally:
        Path(job["result_path"]).write_text(json.dumps(result))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
