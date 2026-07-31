"""Filesystem-backed storage — the default, so local development doesnt need AWS."""

import asyncio
import shutil
from pathlib import Path

from exceptions.exceptions import ExternalServiceException
from external.storage.base import ObjectNotFound, StorageBackend
from logger import logger


class LocalStorageBackend(StorageBackend):
    """Objects as files under a root directory.

    Filesystem calls are synchronous, so each one is pushed to a worker thread —
    a run writing a large frame must not stall the event loop serving every other
    request.

    Some added complexity in the class is due to Win/Linux/MacOS path patterns differences
    This package is meant to be platform-agnostic
    """

    def __init__(self, root: str):
        # A settings value rather than a fixed directory: docker-compose mounts a
        # volume here, which is what stops artifacts dying with the container.
        self.root = Path(root).expanduser().resolve()

    def _path(self, key: str) -> Path:
        # Just a join. Keys only ever come from build_key, which rejects
        # traversal, separators and absolute paths outright — re-checking
        return self.root / key

    async def put(self, key: str, data: bytes) -> str:
        def _write():
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)
        return key

    async def get(self, key: str) -> bytes:
        def _read():
            path = self._path(key)
            if not path.is_file():
                raise ObjectNotFound(key)
            return path.read_bytes()

        return await asyncio.to_thread(_read)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(lambda: self._path(key).is_file())

    async def delete(self, key: str) -> None:
        def _delete():
            self._path(key).unlink(missing_ok=True)

        await asyncio.to_thread(_delete)

    async def list(self, prefix: str) -> list[str]:
        def _list():
            base = self._path(prefix)
            if not base.is_dir():
                return []
            return sorted(
                str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()
            )

        return await asyncio.to_thread(_list)

    async def delete_prefix(self, prefix: str) -> int:
        def _delete_prefix():
            base = self._path(prefix)
            if not base.is_dir():
                return 0
            count = sum(1 for p in base.rglob("*") if p.is_file())
            shutil.rmtree(base, ignore_errors=True)
            return count

        return await asyncio.to_thread(_delete_prefix)

    async def check(self) -> None:
        """Fail at boot if the root is missing or not writable.

        Owns its own failure reporting: what can go wrong here is specific to a
        filesystem
        """

        def _check():
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".write-probe"
            probe.write_bytes(b"")
            probe.unlink()

        try:
            await asyncio.to_thread(_check)
        except OSError as e:
            msg = f"local storage at {self.root} is not writable: {e}"
            logger.error(msg)
            raise ExternalServiceException(msg, "LocalStorageBackend")
        logger.info(f"Local storage ready at {self.root}")
