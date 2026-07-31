"""What `advance` decides, given a set of task rows.

No queue and no database: the decisions are a function of the rows, which is the
property that makes a lost or duplicated message survivable.
"""

import uuid
from dataclasses import dataclass, field

import pytest

from models.run import RunStatus, TaskStatus
from services.orchestrator_service import OrchestratorService
from settings import settings

RUN_ID = uuid.uuid4()


@dataclass
class FakeTask:
    task_id: str
    role: str = "analyst"
    status: str = TaskStatus.PENDING
    attempts: int = 0
    depends_on: list = field(default_factory=list)
    error: str = None


@dataclass
class FakeRun:
    run_id: uuid.UUID = RUN_ID
    status: str = RunStatus.RUNNING
    error: str = None


class FakeQueue:
    def __init__(self):
        self.sent = []

    async def send(self, queue, body, **kw):
        self.sent.append(body)
        return "mid"


@pytest.fixture
def advance(monkeypatch):
    """Run advance over a set of rows and report what it did to them."""
    async def go(tasks, run=None):
        run = run or FakeRun()
        queue = FakeQueue()
        service = OrchestratorService(queue=queue)
        by_id = {t.task_id: t for t in tasks}

        async def get_run(run_id):
            return run

        async def get_tasks(run_id):
            return list(tasks)

        async def set_task_status(task, status, error=None):
            task.status, task.error = status, error
            return task

        async def claim_task(task):
            task.status = TaskStatus.RUNNING
            task.attempts += 1
            return True

        async def set_run_status(r, status, error=None):
            r.status, r.error = status, error
            return r

        from services import orchestrator_service as module
        monkeypatch.setattr(module.run_service, "get_run", get_run)
        monkeypatch.setattr(module.run_service, "get_tasks", get_tasks)
        monkeypatch.setattr(module.run_service, "set_task_status", set_task_status)
        monkeypatch.setattr(module.run_service, "claim_task", claim_task)
        monkeypatch.setattr(module.run_service, "set_run_status", set_run_status)

        await service.advance(RUN_ID)
        return run, by_id, [m["task_id"] for m in queue.sent]

    return go


@pytest.mark.asyncio
async def test_the_frontier_is_dispatched(advance):
    run, tasks, sent = await advance([
        FakeTask("a"), FakeTask("b"), FakeTask("c", depends_on=["a"]),
    ])
    assert sorted(sent) == ["a", "b"], "c waits for a"
    assert tasks["a"].status == TaskStatus.RUNNING, "claimed before dispatch"
    assert tasks["a"].attempts == 1


@pytest.mark.asyncio
async def test_a_failure_with_attempts_left_is_retried(advance):
    run, tasks, sent = await advance([
        FakeTask("a", status=TaskStatus.FAILED, attempts=1, error="boom"),
    ])
    assert sent == ["a"]
    assert tasks["a"].attempts == 2


@pytest.mark.asyncio
async def test_a_task_out_of_attempts_is_not_dispatched_again(advance):
    """The cap that stops a worker and its reviewer disagreeing forever."""
    run, tasks, sent = await advance([
        FakeTask("a", status=TaskStatus.REWORK, attempts=settings.MAX_TASK_ATTEMPTS),
    ])
    assert sent == []
    assert tasks["a"].status == TaskStatus.FAILED
    assert "gave up" in tasks["a"].error
    assert run.status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_dependents_of_a_dead_task_are_superseded(advance):
    """Otherwise they sit pending forever and the run never finishes."""
    run, tasks, sent = await advance([
        FakeTask("a", status=TaskStatus.FAILED,
                 attempts=settings.MAX_TASK_ATTEMPTS, error="no such column"),
        FakeTask("b", depends_on=["a"]),
        FakeTask("c", depends_on=["b"]),
    ])
    assert sent == []
    assert tasks["b"].status == TaskStatus.SUPERSEDED
    assert tasks["c"].status == TaskStatus.SUPERSEDED, "the whole subtree goes"
    assert run.status == RunStatus.FAILED
    assert "no such column" in run.error, "the run says why, not just that"


@pytest.mark.asyncio
async def test_a_finished_run_is_done(advance):
    run, tasks, sent = await advance([
        FakeTask("a", status=TaskStatus.DONE), FakeTask("b", status=TaskStatus.DONE),
    ])
    assert run.status == RunStatus.DONE and run.error is None


@pytest.mark.asyncio
async def test_nothing_happens_to_a_run_that_is_not_running(advance):
    run, tasks, sent = await advance(
        [FakeTask("a")], run=FakeRun(status=RunStatus.AWAITING_APPROVAL)
    )
    assert sent == [] and tasks["a"].status == TaskStatus.PENDING
