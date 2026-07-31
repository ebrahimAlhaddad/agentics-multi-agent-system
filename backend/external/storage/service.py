"""The storage seam.

Callers talk to storage_service and never to a backend directly, so moving from
a local directory to S3 in Stage 2 is a settings change rather than a code
change. This is one of the three seams DESIGN.md relies on to keep the
distributed step additive.
"""

from typing import Optional

from external.base import ExternalService
from external.storage.base import ObjectNotFound, StorageBackend, build_key
from external.storage.local import LocalStorageBackend
from settings import settings
from logger import logger
from exceptions.exceptions import ExternalServiceException


class StorageService(ExternalService):
    """Object storage for anything too large to sit in a database row.

    Bytes in, bytes out. Meaning — parquet, chart specs, row counts — belongs to
    artifact_service one layer up.
    """

    def __init__(self):
        self.backend: Optional[StorageBackend] = None

    def _build_backend(self) -> StorageBackend:
        kind = (settings.STORAGE_BACKEND or "local").lower()
        if kind == "local":
            return LocalStorageBackend(settings.STORAGE_LOCAL_PATH)
        if kind == "s3":
            # Imported lazily so local development never needs the S3 path.
            from external.storage.s3 import S3StorageBackend

            if not settings.STORAGE_S3_BUCKET:
                raise ExternalServiceException(
                    "STORAGE_BACKEND=s3 requires STORAGE_S3_BUCKET", "StorageService"
                )
            return S3StorageBackend(
                bucket=settings.STORAGE_S3_BUCKET,
                prefix=settings.STORAGE_S3_PREFIX or "",
                region=settings.DEFAULT_AWS_REGION,
            )
        raise ExternalServiceException(
            f"unknown STORAGE_BACKEND {kind!r}, expected 'local' or 's3'", "StorageService"
        )

    async def startup(self) -> None:
        logger.info("Starting up Storage Service")
        self.backend = self._build_backend()
        logger.info(f"Storage backend: {settings.STORAGE_BACKEND}")

    async def sanity_check(self) -> None:
        # Each backend reports its own failure, in its own terms — a read-only
        # volume and a denied bucket need different fixes, and neither is
        # improved by being rewrapped here.
        await self.backend.check()

    async def shutdown(self) -> None:
        self.backend = None
        logger.info("Storage Service shut down")

    # ------------------------------------------------------------- operations

    @staticmethod
    def key(*segments: str) -> str:
        """Build a validated storage key. The only supported way to make one."""
        return build_key(*segments)

    async def put(self, key: str, data: bytes) -> str:
        return await self.backend.put(key, data)

    async def get(self, key: str) -> bytes:
        return await self.backend.get(key)

    async def exists(self, key: str) -> bool:
        return await self.backend.exists(key)

    async def delete(self, key: str) -> None:
        await self.backend.delete(key)

    async def list(self, prefix: str) -> list[str]:
        return await self.backend.list(prefix)

    async def delete_prefix(self, prefix: str) -> int:
        return await self.backend.delete_prefix(prefix)


storage_service = StorageService()

__all__ = ["StorageService", "storage_service", "ObjectNotFound", "build_key"]
