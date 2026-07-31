"""The task consumer: python -m workers.task

Reads a message, looks up the worker its `handler` names, and runs it. Everything
the worker needs beyond that it reads from the database itself.

"""

from uuid import UUID

from workers.consumer import run
from models.queue_message import Delivery
from models.queue_message import QueueMessage
from models.run import TaskStatus
from services.agents.roles import WORKER_HANDLERS
from services.agents.roles.base import TaskOutcome
from services.orchestrator_service import orchestrator_service
from services.run_service import run_service
from settings import settings
from logger import logger


async def handle(delivery: Delivery) -> None:
    message = QueueMessage.from_body(delivery.body)
    run_id = UUID(message.run_id)

    try:
        worker = WORKER_HANDLERS[message.handler]
    except KeyError:
        # faulty handler key fails a task
        outcome = TaskOutcome(
            status=TaskStatus.FAILED,
            error=f"no worker for role {message.handler!r}; "
            f"known: {sorted(WORKER_HANDLERS)}",
        )
    else:
        try:
            outcome = await worker(message)
        except Exception as e:
            logger.exception(f"{message.handler} {message.task_id} raised")
            outcome = TaskOutcome(
                status=TaskStatus.FAILED, error=f"{type(e).__name__}: {e}"
            )

    # update task status
    task = await run_service.get_task(run_id, message.task_id)
    if task is not None:
        await run_service.set_task_status(task, outcome.status, error=outcome.error)

    logger.info(
        f"{message.handler} {message.task_id} finished as {outcome.status}"
        + (f": {outcome.error}" if outcome.error else "")
    )
    # request advance always
    await orchestrator_service.request_advance(
        run_id, cause=f"task:{message.task_id}:{task.attempts if task else 0}"
    )


if __name__ == "__main__":
    run(settings.QUEUE_TASKS, handle)
