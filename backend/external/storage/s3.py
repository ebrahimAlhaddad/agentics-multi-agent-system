"""S3-backed storage — the deployed implementation.

boto3 is synchronous, so every call is pushed to a worker thread. Swapping this
for aioboto3 later would not change the interface.
"""

import asyncio

import boto3
from botocore.exceptions import ClientError

from exceptions.exceptions import ExternalServiceException
from external.storage.base import ObjectNotFound, StorageBackend
from logger import logger


class S3StorageBackend(StorageBackend):
    def __init__(self, bucket: str, prefix: str = "", region: str | None = None):
        self.bucket = bucket
        # A prefix lets dev and prod share a bucket without colliding.
        self.prefix = prefix.strip("/")
        self.client = boto3.client("s3", region_name=region)

    def _full(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def _strip(self, key: str) -> str:
        if self.prefix and key.startswith(f"{self.prefix}/"):
            return key[len(self.prefix) + 1 :]
        return key

    async def put(self, key: str, data: bytes) -> str:
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=self._full(key), Body=data
        )
        return key

    async def get(self, key: str) -> bytes:
        def _get():
            try:
                resp = self.client.get_object(Bucket=self.bucket, Key=self._full(key))
                return resp["Body"].read()
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    raise ObjectNotFound(key) from e
                raise

        return await asyncio.to_thread(_get)

    async def exists(self, key: str) -> bool:
        def _head():
            try:
                self.client.head_object(Bucket=self.bucket, Key=self._full(key))
                return True
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    return False
                raise

        return await asyncio.to_thread(_head)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self.client.delete_object, Bucket=self.bucket, Key=self._full(key)
        )

    async def list(self, prefix: str) -> list[str]:
        def _list():
            keys: list[str] = []
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=self.bucket, Prefix=self._full(prefix)
            ):
                keys.extend(self._strip(o["Key"]) for o in page.get("Contents", []))
            return sorted(keys)

        return await asyncio.to_thread(_list)

    async def delete_prefix(self, prefix: str) -> int:
        def _delete_prefix():
            paginator = self.client.get_paginator("list_objects_v2")
            deleted = 0
            for page in paginator.paginate(
                Bucket=self.bucket, Prefix=self._full(prefix)
            ):
                batch = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if not batch:
                    continue
                # delete_objects caps at 1000 per call, which is exactly the page
                # size the paginator yields, so a page is always a legal batch.
                self.client.delete_objects(
                    Bucket=self.bucket, Delete={"Objects": batch}
                )
                deleted += len(batch)
            return deleted

        return await asyncio.to_thread(_delete_prefix)

    async def check(self) -> None:
        """Fail at boot if the bucket is missing or credentials are wrong."""
        try:
            await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)
        except ClientError as e:
            msg = f"S3 bucket {self.bucket!r} is not usable: {e}"
            logger.error(msg)
            raise ExternalServiceException(msg, "S3StorageBackend")
        logger.info(f"S3 storage ready at s3://{self.bucket}/{self.prefix}")
