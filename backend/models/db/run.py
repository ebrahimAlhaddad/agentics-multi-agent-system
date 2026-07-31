from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from datetime import datetime
import uuid

from models.db.base import Base


class Run(Base):
    """A single analysis run (conversation and plan execution)"""

    __tablename__ = "runs"

    run_id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.session_id"),
        nullable=False,
        index=True,
    )
    #: Which datasets this run works with, as a list of dataset ids. A scope
    #: rather than a summary: the planner sets it during the conversation,
    #: before any task exists, and re-planning replaces it
    inputs = Column(JSONB, nullable=True)

    #: The intent, written by the planner. `question` is what was asked;
    #: `approach` is why this graph answers it. Both exist for the
    #: faithfulness check
    question = Column(String, nullable=True)
    approach = Column(String, nullable=True)
    # pending | planning | awaiting_approval | running | done | failed
    status = Column(String, nullable=False, default="pending", index=True)
    error = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
