"""The queue seam.

ElasticMQ speaks the SQS wire protocol, so "local" and "deployed" differ by an
endpoint URL rather than by an implementation

This module knows SQS and nothing else

boto3 is synchronous, so every call goes to a worker thread. With a 20 second long poll a bare receive_message would block the event loop for the whole poll

"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from exceptions.exceptions import ExternalServiceException
from logger import logger
from models.queue_message import Delivery
from settings import settings

from external.base import ExternalService


class QueueService(ExternalService):
    """Message passing between the API, the orchestrator and the workers."""

    def __init__(self):
        self.client = None
        # Queue name -> URL. Resolved once; SQS queue URLs should be stable for the
        # life of the queue
        self._urls: dict[str, str] = {}

    # ------------------------------------------------------------- lifecycle

    async def startup(self) -> None:
        logger.info("Starting up Queue Service")
        endpoint = settings.QUEUE_ENDPOINT_URL or None
        kwargs: dict[str, Any] = {"region_name": settings.DEFAULT_AWS_REGION}
        if endpoint:
            kwargs["endpoint_url"] = endpoint

        key, secret = settings.AWS_ACCESS_KEY, settings.AWS_SECRET_ACCESS_KEY
        if endpoint and not (key and secret):
            # ElasticMQ setup
            key = secret = "local"
        if key and secret:
            # AWS SQS setup
            kwargs["aws_access_key_id"] = key
            kwargs["aws_secret_access_key"] = secret

        self.client = boto3.client("sqs", **kwargs)
        self._urls = {}
        logger.info(f"Queue endpoint: {endpoint or 'AWS SQS'}")

    async def sanity_check(self) -> None:
        """Resolve every queue the app needs, at boot. Pairs a sanity check as well"""
        for name in (settings.QUEUE_RUNS, settings.QUEUE_TASKS):
            await self.url(name)
        logger.info(f"Queues ready: {settings.QUEUE_RUNS}, {settings.QUEUE_TASKS}")

    async def shutdown(self) -> None:
        self.client = None
        self._urls = {}
        logger.info("Queue Service shut down")

    # ------------------------------------------------------------- operations

    async def url(self, name: str) -> str:
        """The URL for a queue name, cached."""
        if name not in self._urls:
            try:
                resp = await asyncio.to_thread(
                    self.client.get_queue_url, QueueName=name
                )
            except (ClientError, BotoCoreError) as e:
                raise ExternalServiceException(
                    f"queue {name!r} is not reachable: {e}", "QueueService"
                ) from e
            self._urls[name] = resp["QueueUrl"]
        return self._urls[name]

    async def send(self, queue: str, body: Mapping[str, Any]) -> str:
        """Publish one message. Returns the message id."""
        try:
            resp = await asyncio.to_thread(
                self.client.send_message,
                QueueUrl=await self.url(queue),
                MessageBody=json.dumps(body),
            )
        except (ClientError, BotoCoreError) as e:
            raise ExternalServiceException(
                f"could not publish to {queue!r}: {e}", "QueueService"
            ) from e
        return resp["MessageId"]

    async def receive(
        self,
        queue: str,
        *,
        max_messages: int = 1,
        wait_seconds: Optional[int] = None,
    ) -> list[Delivery]:
        """Take up to `max_messages` messages, long polling by default.

        An empty list is the normal quiet case, not an error. Received messages
        stay invisible for the queue's visibility timeout and come back if they
        are not deleted, so a consumer that crashes mid-handle loses nothing.
        """
        url = await self.url(queue)
        wait = settings.QUEUE_WAIT_SECONDS if wait_seconds is None else wait_seconds
        try:
            resp = await asyncio.to_thread(
                self.client.receive_message,
                QueueUrl=url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=wait,
                # Without this the response carries no ApproximateReceiveCount,
                # which is how a redelivery is told from a first attempt.
                AttributeNames=["All"],
            )
        except (ClientError, BotoCoreError) as e:
            raise ExternalServiceException(
                f"could not receive from {queue!r}: {e}", "QueueService"
            ) from e

        messages: list[Delivery] = []
        for m in resp.get("Messages", []):
            attrs = m.get("Attributes", {})
            messages.append(
                Delivery(
                    message_id=m["MessageId"],
                    body=json.loads(m["Body"]),
                    receipt_handle=m["ReceiptHandle"],
                    receive_count=int(attrs.get("ApproximateReceiveCount", 1)),
                )
            )
        return messages

    async def delete(self, queue: str, receipt_handle: str) -> None:
        """Acknowledge a message. Until this lands, the message comes back after
        visibility timeout"""
        try:
            await asyncio.to_thread(
                self.client.delete_message,
                QueueUrl=await self.url(queue),
                ReceiptHandle=receipt_handle,
            )
        except (ClientError, BotoCoreError) as e:
            raise ExternalServiceException(
                f"could not delete from {queue!r}: {e}", "QueueService"
            ) from e

    async def extend_visibility(
        self, queue: str, receipt_handle: str, seconds: int
    ) -> None:
        """Keep a message hidden for longer, or hand it straight back.

        A handler that is still working past the visibility timeout must extend
        it or the message is redelivered while it is still running. Passing 0
        does the opposite — it releases the message immediately, which is how a
        consumer declines work it cannot do without waiting out the timeout.
        """
        try:
            await asyncio.to_thread(
                self.client.change_message_visibility,
                QueueUrl=await self.url(queue),
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=seconds,
            )
        except (ClientError, BotoCoreError) as e:
            raise ExternalServiceException(
                f"could not change visibility on {queue!r}: {e}", "QueueService"
            ) from e


queue_service = QueueService()

__all__ = ["Delivery", "QueueService", "queue_service"]
