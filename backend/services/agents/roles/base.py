"""What a worker reports back.

A worker is a module in this package with three names: `DESCRIPTION`, `TERMINAL`
and `handle`. There is no base class and no registration — `WORKERS` in
__init__.py maps a role to the module.
"""

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class TaskOutcome:
    """What a worker reports back.

    Note what is *not* here: an advance. The consumer publishes one after every
    settled task, success or failure — that is what keeps the orchestrator
    level-triggered. If a worker could choose not to advance, forgetting to would
    hang the run.
    """

    status: str
    #: Names of artifacts written, resolvable through artifact_service.
    artifacts: Sequence[str] = ()
    error: Optional[str] = None
