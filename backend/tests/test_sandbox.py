"""Tests for the execution sandbox.

These spawn real child processes — the isolation is the thing under test, so
faking it would test nothing. The gates that used to live in validation_service
are here too, because they are now `ExecutionResult.check()`.
"""

import pandas as pd
import pytest

from external.sandbox import (
    ExecutionResult, MIN_CHART_POINTS, Output, SandboxService, _truncate,
)

MB = 1024 * 1024


def a_sandbox(timeout_s=20, max_output_bytes=50 * MB) -> SandboxService:
    box = SandboxService()
    box.timeout_s, box.max_output_bytes = timeout_s, max_output_bytes
    return box


@pytest.fixture
def sandbox():
    return a_sandbox()


@pytest.fixture
def frame(tmp_path):
    path = tmp_path / "cohorts.parquet"
    pd.DataFrame({"cohort": ["a", "b", "c"], "mrr": [1.0, 2.0, 3.0]}).to_parquet(path)
    return path


# ------------------------------------------------------------------ happy path


@pytest.mark.asyncio
async def test_runs_code_and_emits_a_frame(sandbox):
    result = await sandbox.run(
        "import pandas as pd\n"
        "emit('out', pd.DataFrame({'a': [1, 2]}))"
    )
    assert result.ok, result.error
    assert result.outputs["out"].kind == "frame"
    pd.testing.assert_frame_equal(result.outputs["out"].value, pd.DataFrame({"a": [1, 2]}))


@pytest.mark.asyncio
async def test_loads_a_declared_input(sandbox, frame):
    result = await sandbox.run(
        "df = load('cohorts')\n"
        "emit('total', {'sum': float(df['mrr'].sum())})",
        inputs={"cohorts": frame},
    )
    assert result.ok, result.error
    assert result.outputs["total"].value == {"sum": 6.0}


@pytest.mark.asyncio
async def test_emits_chart_and_report_kinds(sandbox):
    result = await sandbox.run(
        "emit('spec', {'type': 'line'})\n"
        "emit('summary', 'revenue is flat')"
    )
    assert result.ok
    assert result.outputs["spec"].kind == "chart"
    assert result.outputs["summary"].kind == "report"


@pytest.mark.asyncio
async def test_any_file_written_to_out_is_an_output(sandbox):
    """The whole protocol: a file is an artifact. No type dispatch involved."""
    result = await sandbox.run(
        "(out / 'trend.png').write_bytes(b'\\x89PNG\\r\\n\\x1a\\n fake')\n"
        "(out / 'model.pkl').write_bytes(b'pickled bytes')"
    )
    assert result.ok, result.error
    assert result.outputs["trend"].kind == "file"
    assert result.outputs["trend"].value.startswith(b"\x89PNG")
    assert result.outputs["model"].value == b"pickled bytes"


@pytest.mark.asyncio
async def test_matplotlib_style_savefig_works(sandbox):
    """The case the old emit() could not express at all."""
    result = await sandbox.run(
        "with open(out / 'chart.pdf', 'wb') as f:\n"
        "    f.write(b'%PDF-1.4 pretend')"
    )
    assert result.ok, result.error
    assert result.outputs["chart"].kind == "file"


@pytest.mark.asyncio
async def test_stdout_is_captured(sandbox):
    result = await sandbox.run("print('rows processed: 42')")
    assert result.ok and "rows processed: 42" in result.stdout


# --------------------------------------------------------------------- errors


@pytest.mark.asyncio
async def test_exception_becomes_data_not_a_raise(sandbox):
    result = await sandbox.run("1 / 0")
    assert not result.ok
    assert result.error_type == "ZeroDivisionError"
    assert "ZeroDivisionError" in result.as_feedback()


@pytest.mark.asyncio
async def test_syntax_error_is_reported(sandbox):
    result = await sandbox.run("def broken(:\n  pass")
    assert not result.ok and result.error_type == "SyntaxError"


@pytest.mark.asyncio
async def test_traceback_omits_runner_internals(sandbox):
    """The model should see its own frames, not this repo's plumbing."""
    result = await sandbox.run("raise ValueError('bad column')")
    assert "bad column" in result.error
    assert "sandbox_runner" not in result.error


@pytest.mark.asyncio
async def test_loading_an_undeclared_input_fails_usefully(sandbox, frame):
    result = await sandbox.run("load('not_mine')", inputs={"cohorts": frame})
    assert not result.ok
    assert "not_mine" in result.error and "cohorts" in result.error


@pytest.mark.asyncio
async def test_emit_of_an_unhandled_type_says_what_to_do_instead(sandbox):
    result = await sandbox.run("emit('x', 42)")
    assert not result.ok and result.error_type == "TypeError"
    assert "write it to `out`" in result.error


@pytest.mark.asyncio
async def test_partial_outputs_survive_a_later_failure(sandbox):
    """A task that half-worked should still show what it produced."""
    result = await sandbox.run(
        "emit('good', {'a': 1})\n"
        "raise RuntimeError('then it broke')"
    )
    assert not result.ok
    assert "good" in result.outputs


@pytest.mark.asyncio
async def test_output_over_the_cap_is_refused():
    """Nothing bounded analyst output before; a join can produce gigabytes."""
    small = a_sandbox(max_output_bytes=1024)
    result = await small.run("(out / 'big.txt').write_text('x' * 5000)")
    assert not result.ok
    assert result.error_type == "OutputTooLarge"
    assert "Aggregate before writing" in result.error


# ------------------------------------------------------------------ isolation


@pytest.mark.asyncio
async def test_infinite_loop_is_killed():
    sandbox = a_sandbox(timeout_s=2)
    result = await sandbox.run("while True:\n  pass")
    assert not result.ok and result.timed_out
    assert "timed out" in result.as_feedback().lower()


@pytest.mark.asyncio
async def test_a_slow_task_can_ask_for_longer(sandbox):
    """One global timeout was the only knob; a legitimate long job needs more."""
    quick = a_sandbox(timeout_s=1)
    assert (await quick.run("import time; time.sleep(2)")).timed_out
    assert (await quick.run("import time; time.sleep(2)", timeout_s=10)).ok


@pytest.mark.asyncio
async def test_code_cannot_see_the_repository(sandbox):
    """cwd is a scratch directory, so a stray relative write stays contained."""
    result = await sandbox.run(
        "import os\n"
        "emit('listing', {'entries': sorted(os.listdir('.'))})"
    )
    assert result.ok
    entries = result.outputs["listing"].value["entries"]
    assert "app" not in entries and "pyproject.toml" not in entries


@pytest.mark.asyncio
async def test_credentials_are_not_inherited(sandbox, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    result = await sandbox.run(
        "import os\n"
        "emit('env', {'key': os.environ.get('OPENAI_API_KEY', 'ABSENT')})"
    )
    assert result.ok
    assert result.outputs["env"].value["key"] == "ABSENT"


@pytest.mark.asyncio
async def test_outputs_survive_scratch_cleanup(sandbox):
    """Outputs are read into memory, so they outlive the temp directory."""
    result = await sandbox.run("emit('x', {'a': 1})")
    assert result.ok
    assert result.outputs["x"].value == {"a": 1}


@pytest.mark.asyncio
async def test_runs_even_with_a_relative_pythonpath(sandbox, monkeypatch):
    """Regression: the child runs with cwd inside its scratch dir, so a relative
    inherited PYTHONPATH resolved there and the runner was not importable.

    Caught by starting the real server, not by the suite — pytest puts the repo
    on sys.path rather than in the environment."""
    monkeypatch.setenv("PYTHONPATH", ".")
    result = await sandbox.run("emit('ok', {'v': 1})")
    assert result.ok, result.error
    assert result.outputs["ok"].value == {"v": 1}


# ----------------------------------------------------------------- the gates
# Previously validation_service. These need no subprocess: they judge a result.


def result_with(**outputs) -> ExecutionResult:
    return ExecutionResult(ok=True, outputs={
        name: Output(kind=kind, value=value)
        for name, (kind, value) in outputs.items()
    })


FRAME = pd.DataFrame({"cohort": ["a", "b"], "mrr": [1.0, 2.0]})


def test_a_failed_execution_is_rejected_with_its_error():
    assert ExecutionResult(ok=False, error="boom").check(["out"]) == "boom"


def test_a_failure_with_no_error_still_reads_as_one():
    assert ExecutionResult(ok=False).check([])


def test_expecting_nothing_passes():
    assert ExecutionResult(ok=True).check([]) is None


def test_a_missing_output_names_what_was_expected():
    problem = result_with(other=("frame", FRAME)).check(["cohorts"])
    assert "cohorts" in problem and "other" in problem


def test_extra_outputs_are_allowed():
    assert result_with(a=("frame", FRAME), bonus=("chart", {"x": 1})).check(["a"]) is None


def test_an_empty_frame_is_rejected():
    assert "no rows" in result_with(out=("frame", pd.DataFrame({"a": []}))).check(["out"])


def test_an_all_null_frame_is_rejected():
    df = pd.DataFrame({"a": [None, None]})
    assert "only nulls" in result_with(out=("frame", df)).check(["out"])


def test_a_frame_with_data_passes():
    assert result_with(out=("frame", FRAME)).check(["out"]) is None


def test_a_one_point_chart_is_rejected():
    assert str(MIN_CHART_POINTS) in result_with(c=("chart", {"data": [1]})).check(["c"])


def test_an_empty_chart_series_is_rejected():
    """The `or`-chain bug: [] is the degenerate case, not a missing key."""
    assert result_with(c=("chart", {"data": []})).check(["c"])


def test_a_single_repeated_value_chart_is_rejected():
    assert "repeated value" in result_with(c=("chart", {"values": [3, 3, 3]})).check(["c"])


def test_a_real_chart_passes():
    assert result_with(c=("chart", {"data": [1, 2, 3]})).check(["c"]) is None


def test_a_chart_without_a_series_key_is_not_judged():
    assert result_with(c=("chart", {"type": "line"})).check(["c"]) is None


def test_an_empty_report_is_rejected():
    assert result_with(r=("report", "   ")).check(["r"])


def test_a_written_report_passes():
    assert result_with(r=("report", "revenue is flat")).check(["r"]) is None


def test_an_empty_file_output_is_rejected():
    assert result_with(f=("file", b"")).check(["f"])


def test_a_file_output_with_bytes_passes():
    assert result_with(f=("file", b"\x89PNG")).check(["f"]) is None


# ------------------------------------------------------------------ shaping


def test_truncate_leaves_short_text_alone():
    assert _truncate("short") == "short"


def test_truncate_marks_what_it_dropped():
    out = _truncate("x" * 5000, limit=100)
    assert out.startswith("x" * 100)
    assert "truncated 4900 more characters" in out


@pytest.mark.asyncio
async def test_huge_stdout_is_truncated(sandbox):
    """A 5000-row dump must not reach the context window intact."""
    result = await sandbox.run("print('y' * 50000)")
    assert result.ok
    assert len(result.stdout) < 5000
    assert "truncated" in result.stdout


def test_feedback_is_empty_on_success():
    assert ExecutionResult(ok=True).as_feedback() == ""
