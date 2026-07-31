"""The consumer loop. One copy, for every listener.

A consumer is a queue name and a handler. Everything else — starting the
external services, long polling, acknowledging only on success, shutting down
when the platform asks — is identical for all of them and lives here
"""

import asyncio
import signal
from typing import Awaitable, Callable

from external.base import external_services
from external.queue import queue_service
from models.queue_message import Delivery
from logger import logger

#: A handler takes one message and either returns, meaning the message is done
#: and may be deleted, or raises, meaning it must come back.
Handler = Callable[[Delivery], Awaitable[None]]


async def consume(queue: str, handler: Handler) -> None:
    """Start the services, then handle messages from `queue` until asked to stop.

    Acknowledgement is deliberately on the success path only. A handler that
    raises leaves the message untouched, so it reappears after the visibility
    timeout and eventually lands in that queue's dead letter queue. Deleting in
    a `finally` would quietly discard the retry the queue is configured for.
    """
    logger.info("Starting up consumer...")

    services = external_services()
    for service in services:
        name = type(service).__name__
        logger.info(f"Initializing {name}...")
        await service.startup()
        await service.sanity_check()
        logger.info(f"{name} initialized successfully")

    logger.info("All services initialized successfully")

    stopping = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # A deploy sends SIGTERM. Without this the process dies mid-handler and
        # the message waits out its visibility timeout before anyone retries it.
        loop.add_signal_handler(sig, stopping.set)

    logger.info(f"Consuming {queue}")
    try:
        while not stopping.is_set():
            # One at a time. Workers should scale by instance in deployment for now
            messages = await queue_service.receive(queue, max_messages=1)
            for message in messages:
                try:
                    await handler(message)
                except Exception:
                    logger.exception(
                        f"{queue}: handler failed on {message.message_id} "
                        f"(delivery {message.receive_count}); leaving it queued"
                    )
                else:
                    await queue_service.delete(queue, message.receipt_handle)
    finally:
        # Runs on SIGTERM and on an unexpected error alike — a consumer that
        # exits without closing its database pool leaks a connection per deploy.
        logger.info("Shutting down consumer...")
        for service in services:
            name = type(service).__name__
            logger.info(f"Shutting down {name}...")
            await service.shutdown()
            logger.info(f"{name} shut down successfully")
        logger.info(f"Stopped consuming {queue}")


def run(queue: str, handler: Handler) -> None:
    """Entrypoint helper, so each consumer module ends in one line.

    Shutdown can take as long as one long poll: the signal sets the flag, but an
    in-flight receive is a blocking boto3 call on a worker thread and finishes on
    its own. Deployment grace periods must therefore exceed QUEUE_WAIT_SECONDS —
    ECS `stopTimeout` and Kubernetes `terminationGracePeriodSeconds` both default
    to 30s, which is comfortably above the 20s poll.
    """
    asyncio.run(consume(queue, handler))
