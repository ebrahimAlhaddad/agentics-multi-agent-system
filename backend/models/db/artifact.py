from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from datetime import datetime
import uuid

from models.db.base import Base


class Artifact(Base):
    """A named blob in object storage, with a row pointing at it."""

    __tablename__ = "artifacts"

    artifact_id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.session_id"),
        nullable=False,
        index=True,
    )
    #: NULL for an upload — it belongs to the session, not to any one run.
    run_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("runs.run_id"), nullable=True, index=True
    )
    #: NULL for an upload. Nobody produced it.
    task_id = Column(String, nullable=True)

    name = Column(String, nullable=False)
    #: input | transient | terminal — see constants.ArtifactOrigin. This is what
    #: a cleanup pass filters on: transients are sweepable once a run finishes,
    #: inputs and terminals are not.
    origin = Column(String, nullable=False)
    # frame | chart | report
    kind = Column(String, nullable=False)
    object_key = Column(String, nullable=False)

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        # NULLS NOT DISTINCT so two uploads in one session cannot share a name.
        # Without it Postgres treats every NULL run_id as distinct, so the
        # constraint would not bind uploads at all — the one case where a person
        # rather than the planner picks the name.
        UniqueConstraint(
            "session_id",
            "run_id",
            "name",
            name="uq_artifacts_session_run_name",
            postgresql_nulls_not_distinct=True,
        ),
    )


class ArtifactProfile(Base):
    """What is inside an artifact. One row per artifact, or none.

    Stores initial analysis upon upload or EDA
    """

    __tablename__ = "artifact_profiles"

    artifact_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("artifacts.artifact_id", ondelete="CASCADE"),
        primary_key=True,
    )
    row_count = Column(Integer, nullable=True)
    #: [{name, dtype, nulls, cardinality, ...}] — profile_service's output.
    columns = Column(JSONB, nullable=True)

    created_at = Column(DateTime, default=datetime.now)
