"""The orchestrator consumer: python -m workers.orchestrator

Reads advances off the runs queue. A message says only "look at this run", so
everything this needs comes from the task rows.
"""

from uuid import UUID

from workers.consumer import run
from models.queue_message import Delivery
from models.queue_message import QueueMessage
from services.orchestrator_service import orchestrator_service
from settings import settings


async def handle(delivery: Delivery) -> None:
    message = QueueMessage.from_body(delivery.body)
    await orchestrator_service.advance(UUID(message.run_id))


if __name__ == "__main__":
    run(settings.QUEUE_RUNS, handle)
