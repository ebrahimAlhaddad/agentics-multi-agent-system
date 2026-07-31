"""The synthesizer's draft-then-check loop.

Model calls stubbed: what is tested is that an unfaithful draft is not stored,
that the reason reaches the redraft, that a report is only ever written once it
has passed — an unfaithful report is worse than none, because it reads like an
answer — and that the verdict is stored beside it for the reader.
"""

import uuid
from types import SimpleNamespace

import pytest

from models.artifact import ArtifactKind, ArtifactOrigin
from models.queue_message import QueueMessage
from models.review import FAITHFULNESS_NOTE, Review
from models.run import TaskStatus
from services.agents.roles import synthesizer as worker

RUN_ID = uuid.uuid4()


class FakeConversation:
    def __init__(self):
        self.cleared = False

    async def clear_session(self):
        self.cleared = True


@pytest.fixture
def wired(monkeypatch):
    task = SimpleNamespace(
        task_id="n_report",
        description="write it up",
        acceptance="",
        produces=["summary"],
        consumes=["n_mrr/by_cohort"],
        columns=[],
    )
    run = SimpleNamespace(run_id=RUN_ID, question="why flat?", approach="by cohort")
    conversation = FakeConversation()
    state = SimpleNamespace(
        drafts=[],
        stored=[],
        task=task,
        conversation=conversation,
    )

    async def get_task(run_id, task_id):
        return state.task

    async def get_run(run_id):
        return run

    async def summarise(run_id, names):
        return "- n_mrr/by_cohort (table, 3 rows)"

    async def put(name, kind, obj, *, origin=ArtifactOrigin.TRANSIENT, **_):
        state.stored.append((name, kind, obj, origin))

    monkeypatch.setattr(worker.run_service, "get_task", get_task)
    monkeypatch.setattr(worker.run_service, "get_run", get_run)
    monkeypatch.setattr(worker.artifact_service, "summarise", summarise)
    monkeypatch.setattr(worker.artifact_service, "put", put)
    monkeypatch.setattr(worker, "history", lambda *a: conversation)

    def verdicts(*reasons):
        """Queue a reason per round. None means the draft passed."""

        async def draft(run, results_text, conversation, feedback):
            state.drafts.append(feedback)
            return f"draft {len(state.drafts)}"

        async def check(run, results_text, report):
            reason = reasons[len(state.drafts) - 1]
            return Review(
                approved=reason is None,
                reason=reason or "every number appears in the results",
            )

        monkeypatch.setattr(worker, "draft", draft)
        monkeypatch.setattr(worker, "check", check)

    state.verdicts = verdicts
    return state


async def run():
    return await worker.handle(
        QueueMessage(handler="synthesizer", run_id=str(RUN_ID), task_id="n_report")
    )


@pytest.mark.asyncio
async def test_a_faithful_first_draft_is_stored(wired):
    wired.verdicts(None)
    outcome = await run()

    assert outcome.status == TaskStatus.DONE
    assert outcome.artifacts == ("summary",)
    assert wired.stored == [
        # Marked terminal: it is the run's answer, and that is how anything
        # asking for the answer finds it rather than guessing from names.
        ("summary", ArtifactKind.REPORT, "draft 1", ArtifactOrigin.TERMINAL),
        # The verdict travels with it, for the reader to weigh.
        (
            FAITHFULNESS_NOTE,
            ArtifactKind.REPORT,
            "every number appears in the results",
            ArtifactOrigin.TRANSIENT,
        ),
    ]
    assert wired.conversation.cleared


@pytest.mark.asyncio
async def test_an_unfaithful_claim_comes_back_to_the_redraft(wired):
    wired.verdicts("'cohorts are similar' is not in the results", None)
    outcome = await run()

    assert outcome.status == TaskStatus.DONE
    assert wired.drafts == [None, "'cohorts are similar' is not in the results"]
    # Only the draft that passed was written.
    assert [name for name, *_ in wired.stored] == ["summary", FAITHFULNESS_NOTE]
    assert (
        "summary",
        ArtifactKind.REPORT,
        "draft 2",
        ArtifactOrigin.TERMINAL,
    ) in wired.stored


@pytest.mark.asyncio
async def test_nothing_is_stored_when_no_draft_passes(wired):
    """The invariant: a report exists only if it survived the check."""
    wired.verdicts("invented a number", "still invented a number")
    outcome = await run()

    assert outcome.status == TaskStatus.FAILED
    assert outcome.error == "still invented a number"
    assert wired.stored == []
    assert not wired.conversation.cleared


@pytest.mark.asyncio
async def test_a_synthesizer_must_declare_one_report(wired):
    wired.task.produces = ["summary", "appendix"]
    wired.verdicts(None)
    outcome = await run()

    assert outcome.status == TaskStatus.FAILED
    assert "exactly one report" in outcome.error
    assert wired.stored == []
