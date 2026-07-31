"""A run and its tasks: the states they move through, and the wire shapes.

The status vocabulary
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    VALIDATING = "validating"
    REWORK = "rework"
    DONE = "done"
    FAILED = "failed"
    #: Discarded by a re-plan — it consumed something that will never exist.
    SUPERSEDED = "superseded"

    #: Nothing further will happen to a task in one of these states.
    TERMINAL = frozenset({DONE, FAILED, SUPERSEDED})
    #: In flight — the orchestrator must not dispatch these again.
    ACTIVE = frozenset({RUNNING, VALIDATING})
    #: Eligible for dispatch once dependencies are satisfied. REWORK is included
    #: because a rejected task keeps its satisfied dependencies and simply needs
    #: another attempt.
    DISPATCHABLE = frozenset({PENDING, REWORK})


class RunStatus:
    PENDING = "pending"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    #: The question cannot be answered from this data. Distinct from FAILED:
    #: nothing went wrong, the data simply does not contain what was asked for.
    BLOCKED = "blocked"


class RouterRunRequest(BaseModel):
    """Opening a run. Neither the question nor the inputs are here — both come
    out of the conversation."""

    session_id: Optional[UUID] = None


class RouterChatRequest(BaseModel):
    content: str = Field(min_length=1)
    session_id: Optional[UUID] = None


class RouterRunSummary(BaseModel):
    """A row in the run history list."""

    # Load-bearing, not boilerplate: routes return ORM rows directly and let
    # response_model do the conversion, so without from_attributes every one
    # of those routes fails at serialisation.
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    question: Optional[str] = None
    status: str
    inputs: Optional[list[str]] = None
    created_at: Optional[datetime] = None


class TaskState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    role: str
    description: str
    status: str
    attempts: int = 0
    depends_on: list[str] = []
    produces: list[str] = []
    consumes: list[str] = []
    error: Optional[str] = None


class RouterRunDetail(BaseModel):
    """A run plus its task graph and, if it got that far, its answer."""

    run_id: str
    status: str
    question: Optional[str] = None
    error: Optional[str] = None
    report: Optional[str] = None
    #: What the faithfulness check found. Present whenever a report is, and
    #: shown beside it: a reader deciding how far to trust an answer needs to
    #: know it was checked and what the checker could not confirm.
    report_note: Optional[str] = None
    #: The graph, not just the states: every node carries its own depends_on,
    #: so there is nothing a separate plan list would add.
    tasks: list[TaskState] = []
