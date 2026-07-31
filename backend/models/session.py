from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    title: Optional[str] = Field(
        default=None, description="Omitted, the session auto-names from its id."
    )


class SessionResponse(BaseModel):
    # Load-bearing, not boilerplate: routes return ORM rows directly and let
    # response_model do the conversion, so without from_attributes every one
    # of those routes fails at serialisation.
    model_config = ConfigDict(from_attributes=True)

    session_id: UUID
    title: Optional[str] = None
    created: Optional[datetime] = None
