"""What an agent is allowed to act on.

Not a database row and not a request body: a value carried through a single
request, from the router that authorised it down to the tools that use it.

"""

from dataclasses import dataclass
from typing import Optional
from uuid import UUID


@dataclass
class RunContext:
    """Who is asking, and about which run. Server-supplied, never model-supplied.

    Built in exactly one place — `build_context` in the run router — which is
    where user_id is taken from auth, the session is checked against it, and the
    run is checked against the session. Everything downstream treats the session
    on it as already proven and does not re-check.
    """

    user_id: str
    session_id: UUID
    run_id: Optional[UUID] = None
