"""Planner tests with a faked model.

The model call is stubbed so the retry-on-invalid-plan loop can be tested
deterministically — that loop is the point, not the prompt. `make_plan` is a
function over the SDK now rather than a service with an injected client, so the
stub goes in through monkeypatch instead of a constructor.
"""

import uuid
from types import SimpleNamespace

import pytest

from models.run import RunStatus
from models.run_context import RunContext
from models.plan import Plan, TaskNode
from exceptions.exceptions import ServiceLayerException
from services.agents import planner as planner_module


def node(nid, deps=(), produces=("out",), consumes=("input/subs",), role="analyst"):
    return TaskNode(
        id=nid,
        description=f"do {nid}",
        role=role,
        depends_on=list(deps),
        produces=list(produces),
        consumes=list(consumes),
        acceptance="checkable",
    )


def plan(nodes):
    return Plan(nodes=nodes, question="why is revenue flat?", approach="count things")


GOOD = plan(
    [
        node("n_cohorts", produces=["cohorts"]),
        node("n_ret", deps=["n_cohorts"], consumes=["cohorts"]),
        node("n_report", deps=["n_ret"], consumes=["cohorts"], role="synthesizer"),
    ]
)

CYCLIC = plan([node("a", deps=["b"]), node("b", deps=["a"])])

DANGLING = plan([node("a", deps=["ghost"])])

RACY = plan(
    [
        node("root"),
        node("producer", deps=["root"], produces=["mrr_table"]),
        node("consumer", deps=["root"], consumes=["mrr_table"]),
    ]
)

#: A task with nothing to read — the shape validation refuses before a run.
SOURCELESS = plan([node("a", consumes=[])])

#: What the chat agent already put in the session. The planner reads it as
#: input; nothing it does may add to it.
CONVERSATION = [
    {"role": "user", "content": "why is revenue flat?"},
    {"role": "assistant", "content": "Which cohorts do you want compared?"},
]


# ------------------------------------------------------------------- doubles


INPUT_ID = uuid.uuid4()


class FakeRun:
    def __init__(self, status=RunStatus.PLANNING, inputs=(str(INPUT_ID),)):
        self.run_id = uuid.uuid4()
        self.status = status
        #: Artifact ids, as the column holds them — make_plan resolves each to a
        #: name so a root task consuming an input can be validated.
        self.inputs = list(inputs)


class FakeRuns:
    """Only what make_plan touches."""

    def __init__(self, run):
        self.run = run
        self.written: list = []
        self.intent = None
        self.status = None

    async def get_run(self, run_id):
        return self.run

    async def replace_tasks(self, run_id, nodes):
        self.written = nodes
        return nodes

    async def set_intent(self, run, question, approach):
        self.intent = (question, approach)
        return run

    async def set_run_status(self, run, status):
        self.status = status
        return run


class FakeRunner:
    """Stands in for agents.Runner: hands back queued plans, records prompts."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.prompts: list = []
        self.sessions: list = []

    async def run(self, agent, prompt, context=None, session=None, **kwargs):
        self.prompts.append(prompt)
        self.sessions.append(session)
        if not self.outputs:
            raise AssertionError("model called more times than plans provided")
        nxt = self.outputs.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return type("Result", (), {"final_output": nxt})()


@pytest.fixture
def planner(monkeypatch):
    """Wire the module's collaborators to doubles, and hand back the knobs."""

    def build(*outputs, run=None, max_attempts=3, verdicts=None):
        """`outputs` are the plans the model emits; `verdicts` what the validator
        says about each structurally sound one — None approves."""
        run = run or FakeRun()
        runs, runner = FakeRuns(run), FakeRunner(*outputs)

        async def get_artifact(*, artifact_id, session_id=None, **_):
            return SimpleNamespace(name="input/subs")

        judged, discarded = [], []

        async def judge(plan, inputs):
            judged.append(plan)
            if verdicts is None:
                return None
            return verdicts[len(judged) - 1]

        async def describe(names, **scope):
            return "- input/subs: cohort, mrr"

        async def delete(**filters):
            """Re-planning throws away what the old plan produced."""
            discarded.append(filters["run_id"])
            return 0

        monkeypatch.setattr(planner_module.artifact_service, "delete", delete)
        monkeypatch.setattr(planner_module, "judge", judge)
        monkeypatch.setattr(planner_module.artifact_service, "describe", describe)
        monkeypatch.setattr(planner_module.artifact_service, "resolve", get_artifact)
        monkeypatch.setattr(planner_module, "run_service", runs)
        monkeypatch.setattr(planner_module, "Runner", runner)

        # The real one opens a SQLAlchemy-backed SDK session against Postgres.
        # plan_agent only reads it, so an empty transcript is enough.
        class FakeHistory:
            async def get_items(self):
                return list(CONVERSATION)

        monkeypatch.setattr(planner_module, "history", lambda run_id: FakeHistory())
        monkeypatch.setattr(planner_module, "MAX_PLAN_ATTEMPTS", max_attempts)
        ctx = RunContext(user_id="tester", session_id=uuid.uuid4(), run_id=run.run_id)
        runner.judged, runner.discarded = judged, discarded
        return ctx, runs, runner

    return build


# --------------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_valid_plan_is_accepted_first_time(planner):
    ctx, runs, runner = planner(GOOD)
    result, rejected = await planner_module.make_plan(ctx)

    assert rejected == []
    assert [n.id for n in result.nodes] == ["n_cohorts", "n_ret", "n_report"]
    assert len(runner.prompts) == 1
    # The rows are the plan, so a good plan is written and the run parks.
    assert [n["id"] for n in runs.written] == ["n_cohorts", "n_ret", "n_report"]
    assert runs.status == RunStatus.AWAITING_APPROVAL
    assert runs.intent == ("why is revenue flat?", "count things")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [CYCLIC, DANGLING, RACY])
async def test_invalid_plan_is_retried_and_then_accepted(planner, bad):
    ctx, runs, runner = planner(bad, GOOD)
    await planner_module.make_plan(ctx)
    assert len(runner.prompts) == 2
    assert [n["id"] for n in runs.written] == ["n_cohorts", "n_ret", "n_report"]


@pytest.mark.asyncio
async def test_repeated_invalidity_gives_up_with_the_reasons(planner):
    ctx, runs, runner = planner(CYCLIC, CYCLIC, CYCLIC, max_attempts=3)
    with pytest.raises(ServiceLayerException) as e:
        await planner_module.make_plan(ctx)

    assert "cycle" in str(e.value)
    assert len(runner.prompts) == 3
    # Nothing was written, and the run did not move on.
    assert runs.written == [] and runs.status is None


@pytest.mark.asyncio
async def test_the_rejection_reasons_are_fed_back_to_the_model(planner):
    """Without this the retry is just a reroll, not a repair."""
    ctx, _, runner = planner(DANGLING, GOOD)
    await planner_module.make_plan(ctx)

    last = "".join(str(item) for item in runner.prompts[-1])
    assert "dangling_dependency" in last and "ghost" in last


@pytest.mark.asyncio
async def test_warnings_do_not_block_a_plan(planner):
    """An orphan node is a smell, not a reason to refuse to run."""
    with_orphan = plan(list(GOOD.nodes) + [node("lonely")])
    ctx, runs, runner = planner(with_orphan)
    await planner_module.make_plan(ctx)

    assert len(runner.prompts) == 1
    assert "lonely" in [n["id"] for n in runs.written]


@pytest.mark.asyncio
async def test_a_run_with_no_inputs_is_refused_before_the_model(planner):
    """A plan over nothing to read is not a plan, and the model cannot fix it."""
    ctx, _, runner = planner(GOOD, run=FakeRun(inputs=()))
    with pytest.raises(ServiceLayerException) as e:
        await planner_module.make_plan(ctx)

    assert "use_inputs" in str(e.value)
    assert runner.prompts == []


@pytest.mark.asyncio
async def test_replanning_a_running_run_is_refused(planner):
    """The rows are the plan; replacing them under a run in flight loses state."""
    ctx, _, runner = planner(GOOD, run=FakeRun(status=RunStatus.RUNNING))
    with pytest.raises(ServiceLayerException) as e:
        await planner_module.make_plan(ctx)

    assert "executing" in str(e.value)
    assert runner.prompts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("finished", [RunStatus.FAILED, RunStatus.DONE])
async def test_a_finished_run_can_be_planned_again(planner, finished):
    """Talking to a run that has already run rewrites it. Anything the old plan
    produced goes with it — those results answer a question it is no longer
    asking, and a familiar name resolving to one would be worse than missing."""
    ctx, runs, runner = planner(GOOD, run=FakeRun(status=finished))
    await planner_module.make_plan(ctx)

    assert runs.written, "the new plan replaced the old rows"
    assert runner.discarded == [ctx.run_id], "the old outputs were thrown away"
    assert runs.status == RunStatus.AWAITING_APPROVAL, "it parks for approval again"


@pytest.mark.asyncio
async def test_plan_converts_to_the_shape_the_rest_of_the_system_takes(planner):
    ctx, runs, _ = planner(GOOD)
    await planner_module.make_plan(ctx)
    assert set(runs.written[0]) >= {
        "id",
        "role",
        "description",
        "depends_on",
        "produces",
        "consumes",
        "acceptance",
    }


@pytest.mark.asyncio
async def test_a_structurally_sound_plan_still_faces_the_validator(planner):
    """The check dag_service cannot make: does this plan answer the question."""
    ctx, runs, runner = planner(
        GOOD,
        GOOD,
        verdicts=["n_cohorts uses a column no input has", None],
    )
    await planner_module.make_plan(ctx)

    assert len(runner.prompts) == 2, "the first plan was sent back"
    assert "column no input has" in "".join(str(i) for i in runner.prompts[-1])
    assert runs.written, "the second one was written"


@pytest.mark.asyncio
async def test_the_validator_is_not_asked_about_a_broken_plan(planner):
    """Cheapest first — a cyclic plan never costs a model call."""
    ctx, runs, runner = planner(CYCLIC, GOOD)
    await planner_module.make_plan(ctx)

    assert len(runner.judged) == 1, "only the sound plan was judged"


@pytest.mark.asyncio
async def test_a_plan_the_validator_keeps_rejecting_gives_up(planner):
    ctx, runs, runner = planner(
        GOOD,
        GOOD,
        GOOD,
        max_attempts=3,
        verdicts=["wrong question", "still wrong", "still wrong"],
    )
    with pytest.raises(ServiceLayerException) as e:
        await planner_module.make_plan(ctx)

    assert "still wrong" in str(e.value)
    assert runs.written == [] and runs.status is None


@pytest.mark.asyncio
async def test_the_planner_reads_the_conversation_but_never_writes_to_it(planner):
    """Its turns must not reach the chat session.

    They used to, and `transcript` then had to sort ours from theirs by matching
    our own prompt text — which a person can type verbatim, losing their message
    from their own transcript. Passing no session is what makes that unnecessary.
    """
    ctx, runs, runner = planner(CYCLIC, GOOD)
    await planner_module.make_plan(ctx)

    assert runner.sessions == [None, None], "no attempt may persist its turns"
    # The conversation is still read: it is the input, not the store.
    assert runner.prompts[0][:-1] == CONVERSATION
    assert runner.prompts[0][-1]["content"] == planner_module.PLAN_PROMPT
    # The refused draft and the reason ride along in the retry's own list.
    assert planner_module.REJECTED_PROMPT in runner.prompts[1][-1]["content"]
