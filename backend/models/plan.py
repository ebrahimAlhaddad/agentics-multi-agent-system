"""The planner's output contract.

This schema is the interface between planning and orchestration: the planner
produces it, dag_service validates it, run_service materialises it as task rows.
It is enforced by constrained decoding rather than asked for in prose, so the
SHAPE is guaranteed — the semantics still need validating, which is dag_service's
job.

`produces` and `consumes` are the load-bearing fields. Without named intermediate
artifacts a plan collapses into fan-out/fan-in and its dependencies are
decorative; with them the graph has real depth and a real reason to be walked.

There is deliberately no `columns`. A plan naming the columns each task may read
meant the planner had to commit to a schema before anything had looked at the
data — including the schema of results that did not exist yet — and every way of
getting that wrong failed late, at staging. The task itself can see the data; the
reviewers check what it did with it.
"""

from pydantic import BaseModel, Field


class Severity:
    """How badly a plan problem reads. dag_service returns these on PlanError."""

    ERROR = "error"
    WARNING = "warning"


class TaskNode(BaseModel):
    id: str = Field(description="Short stable identifier, e.g. 'n_cohorts'.")
    description: str = Field(
        description="What this task must do, in one or two sentences, specific "
        "enough that an analyst could implement it without the "
        "original question."
    )
    role: str = Field(
        description="Which kind of worker carries this task out. Use one of the "
        "roles listed in the instructions and nothing else."
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Ids of tasks that must finish first. This is what encodes "
        "ordering — do not rely on list position.",
    )
    produces: list[str] = Field(
        default_factory=list,
        description="Names of intermediate artifacts this task writes, e.g. "
        "'cohorts'. Name anything a later task needs; a downstream "
        "task can only consume what an upstream task produced.",
    )
    consumes: list[str] = Field(
        default_factory=list,
        description="Artifact names this task reads. Every one must be produced "
        "by a task it transitively depends on.",
    )
    acceptance: str = Field(
        default="",
        description="A concrete, checkable criterion for a correct result, e.g. "
        "'one row per cohort, at least 12 cohorts'.",
    )


class Plan(BaseModel):
    nodes: list[TaskNode] = Field(
        description="The task graph. Order is irrelevant; depends_on defines it."
    )

    #: What was asked and why this graph answers it. Not recoverable from the
    #: nodes — they say what will be computed, not what it was for — and a
    #: faithfulness check needs the intent rather than the steps.
    question: str = Field(
        description="The question this plan answers, in one sentence, phrased so "
        "the person who asked would recognise it as theirs."
    )
    approach: str = Field(
        description="How these tasks answer that question: the reasoning that "
        "connects the graph to the question, in two or three sentences."
    )

    def as_dicts(self) -> list[dict]:
        """The shape run_service.create_tasks and dag_service both accept."""
        return [n.model_dump() for n in self.nodes]
