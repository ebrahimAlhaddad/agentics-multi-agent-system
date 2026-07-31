"""Schema, run state, and durable graph resume — against a real Postgres."""


import pytest
from sqlalchemy import text

from models.run import TaskStatus
from services.run_service import run_service


async def _tables(postgres) -> list[str]:
    async with postgres.engine.begin() as conn:
        return list((await conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        ))).scalars().all())


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["runs", "run_tasks", "artifacts", "sessions"])
async def test_startup_creates_tables(postgres, table):
    assert table in await _tables(postgres)


@pytest.mark.asyncio
async def test_array_and_jsonb_columns_round_trip(postgres, session_id):
    """ARRAY(String) is the column type most likely to surprise."""
    run = await run_service.create_run(session_id=session_id, question="why?")
    await run_service.create_tasks(run.run_id, [
        {"id": "n_mrr", "role": "analyst", "description": "build MRR",
         "depends_on": [], "produces": ["mrr"], "consumes": [], "columns": ["mrr"]},
        {"id": "n_ret", "role": "analyst", "description": "retention",
         "depends_on": ["n_mrr"], "produces": [], "consumes": ["mrr"], "columns": ["signup"]},
    ])

    rows = await run_service.get_tasks(run.run_id)
    tasks = {t.task_id: t for t in rows}
    assert tasks["n_ret"].depends_on == ["n_mrr"]
    assert tasks["n_ret"].consumes == ["mrr"]

    # The plan is these rows, in the order the planner emitted them — there is
    # no second representation to rebuild, on the wire or anywhere else.
    assert [t.task_id for t in rows] == ["n_mrr", "n_ret"]


@pytest.mark.asyncio
async def test_status_transition_records_started_at(postgres, session_id):
    run = await run_service.create_run(session_id=session_id)
    await run_service.create_tasks(run.run_id, [
        {"id": "a", "role": "analyst", "description": "x",
         "depends_on": [], "produces": [], "consumes": [], "columns": []},
    ])
    task = await run_service.get_task(run.run_id, "a")
    await run_service.set_task_status(task, TaskStatus.RUNNING)

    reread = await run_service.get_task(run.run_id, "a")
    assert reread.status == TaskStatus.RUNNING
    assert reread.started_at is not None


@pytest.mark.asyncio
async def test_update_actually_writes(postgres, session_id):
    """Regression: PostgresService.update once mutated a detached instance and
    committed an unrelated session, so it logged success and wrote nothing."""
    run = await run_service.create_run(session_id=session_id, question="original")
    await run_service.set_run_status(run, "running", error="first")
    assert (await run_service.get_run(run.run_id)).status == "running"


