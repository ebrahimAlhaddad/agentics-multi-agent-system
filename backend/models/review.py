"""What a critic returns.

Structured output rather than free text, so the verdict is a field the
orchestrator can branch on instead of prose someone has to parse.
"""

from pydantic import BaseModel, Field

#: What a synthesizer stores its faithfulness verdict under, alongside the
#: report it checked
FAITHFULNESS_NOTE = "faithfulness"


class Review(BaseModel):
    approved: bool = Field(
        description="True if the results actually answer the task as specified."
    )
    reason: str = Field(
        description="One or two sentences. On a rejection, say specifically what "
        "is wrong and what would fix it — this is what whoever redoes "
        "the task will read. On an approval, say what you checked."
    )
