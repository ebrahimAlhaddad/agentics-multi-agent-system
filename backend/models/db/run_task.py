from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, ARRAY
from datetime import datetime

from models.db.base import Base


class RunTask(Base):
    """One node of a run's task graph.

    The primary key is (run_id, task_id), which is what makes a task idempotent:
    re-running it after an at-least-once redelivery targets the same row rather
    than creating a second one.

    depends_on / produces / consumes are the graph edges. They are stored but not
    traversed here — dag_service loads a run's tasks once and validates graph structure
    """

    __tablename__ = "run_tasks"

    run_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("runs.run_id"), primary_key=True, index=True
    )
    task_id = Column(String, primary_key=True)

    role = Column(String, nullable=False)
    description = Column(String, nullable=False)
    acceptance = Column(String, nullable=True)

    # pending | running | validating | rework | done | failed
    status = Column(String, nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    error = Column(String, nullable=True)

    depends_on = Column(ARRAY(String), nullable=False, default=list)
    produces = Column(ARRAY(String), nullable=False, default=list)
    consumes = Column(ARRAY(String), nullable=False, default=list)
    # Dropped from the plan: a task no longer declares which columns it may
    # read, because the planner had to guess them before anything had looked at
    # the data. Column kept for one migration so existing rows still load.
    columns = Column(ARRAY(String), nullable=False, default=list)

    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
