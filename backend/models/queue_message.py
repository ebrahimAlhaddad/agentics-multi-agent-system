"""What travels on a queue.

One shape for every queue. A message says which handler should pick it up and
which run — and task, when there is one — it concerns

"""

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class QueueMessage:
    #: Which handler this is for. On the tasks queue that is a worker role; on
    #: the runs queue it is the orchestrator's own action.
    handler: str
    run_id: str
    #: Absent for work that is not a task in a plan — a replan or an advance
    #: concerns the whole run.
    task_id: Optional[str] = None

    def as_body(self) -> dict:
        return asdict(self)

    @classmethod
    def from_body(cls, body: dict) -> "QueueMessage":
        return cls(**body)


@dataclass(frozen=True)
class Delivery:
    """One delivery of a message.

    `receipt_handle` is not an id — it identifies *this delivery*, and a
    redelivery of the same message carries a different one. Deleting requires
    the handle from the delivery you are actually finishing.
    """

    message_id: str
    body: dict
    receipt_handle: str
    # How many times this message has been delivered. 1 on the first attempt,
    # so >1 means a previous consumer took it and did not delete it
    # This signals that a worker died mid-task
    receive_count: int
