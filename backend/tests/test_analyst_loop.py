"""The analyst's write-then-review loop.

The loop is deterministic code around two model calls, so it is tested with the
model calls stubbed: what matters here is that a rejection comes back as
feedback, that rounds are capped, and that a run that never passes fails with
the reason rather than silently reporting success.
"""

import uuid
from types import SimpleNamespace

import pytest

from models.queue_message import QueueMessage
from models.run import TaskStatus
from services.agents.roles import analyst as worker

RUN_ID = uuid.uuid4()


class FakeConversation:
    def __init__(self):
        self.cleared = False

    async def clear_session(self):
        self.cleared = True


@pytest.fixture
def wired(monkeypatch):
    """Stub everything around the loop, and record how it was driven."""
    task = SimpleNamespace(
        task_id="n_mrr",
        description="totals per cohort",
        acceptance="one row each",
        produces=["totals"],
        consumes=[],
        columns=[],
    )
    conversation = FakeConversation()
    calls: list = []

    async def get_task(run_id, task_id):
        return task

    async def stage(run_id, names, directory, columns=()):
        return {}

    monkeypatch.setattr(worker.run_service, "get_task", get_task)
    monkeypatch.setattr(worker.artifact_service, "stage", stage)
    monkeypatch.setattr(worker, "history", lambda *a: conversation)

    def attempts(*verdicts):
        """Queue up what each round's review returns: None approves."""

        async def attempt(task, state, produces, feedback):
            calls.append(feedback)
            return verdicts[len(calls) - 1]

        monkeypatch.setattr(worker, "attempt", attempt)

    return SimpleNamespace(attempts=attempts, calls=calls, conversation=conversation)


async def run() -> object:
    return await worker.handle(
        QueueMessage(handler="analyst", run_id=str(RUN_ID), task_id="n_mrr")
    )


@pytest.mark.asyncio
async def test_an_accepted_result_finishes_on_the_first_round(wired):
    wired.attempts(None)
    outcome = await run()

    assert outcome.status == TaskStatus.DONE
    assert outcome.artifacts == ("totals",)
    assert wired.calls == [None], "the first round has no feedback to act on"
    assert wired.conversation.cleared, "a finished task leaves no transcript"


@pytest.mark.asyncio
async def test_a_rejection_comes_back_as_feedback(wired):
    """The point of the loop: round two is told why round one was rejected."""
    wired.attempts("you grouped by plan, not cohort", None)
    outcome = await run()

    assert outcome.status == TaskStatus.DONE
    assert wired.calls == [None, "you grouped by plan, not cohort"]


@pytest.mark.asyncio
async def test_rounds_are_capped_and_the_reason_survives(wired):
    """Two rejections is the end of it — the run decides what happens next."""
    wired.attempts("still wrong", "still wrong twice")
    outcome = await run()

    assert outcome.status == TaskStatus.FAILED
    assert outcome.error == "still wrong twice"
    assert len(wired.calls) == worker.MAX_REVIEW_ROUNDS
    assert not wired.conversation.cleared, "a failed task keeps its transcript"


@pytest.mark.asyncio
async def test_a_broken_worker_fails_the_task_rather_than_raising(wired, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(worker, "attempt", boom)

    outcome = await run()
    assert outcome.status == TaskStatus.FAILED
    assert "rate limited" in outcome.error
