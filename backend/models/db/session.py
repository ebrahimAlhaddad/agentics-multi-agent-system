from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
import uuid
from datetime import datetime
from models.db.base import Base


class Session(Base):
    """A workspace, with its own datasets and runs.

    session_id is the primary key and user_id is a plain indexed column.
    """

    __tablename__ = "sessions"

    # unique constraint cannot be dropped after some changes. Keep for now although redundant
    session_id = Column(
        PG_UUID(as_uuid=True), primary_key=True, unique=True, default=uuid.uuid4
    )
    user_id = Column(String, nullable=False, index=True)

    title = Column(String, nullable=True)
    created = Column(DateTime, default=datetime.now)
    last_activity = Column(DateTime, default=datetime.now)
