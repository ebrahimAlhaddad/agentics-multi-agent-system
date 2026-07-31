"""Storage backend contract.

Deliberately bytes-in / bytes-out. Nothing here knows what a dataframe, a chart
spec or a parquet file is — that meaning lives one layer up in artifact_service.
Keeping this layer dumb is what lets the local and S3 implementations stay
interchangeable.
"""

from abc import ABC, abstractmethod
import re


class ObjectNotFound(Exception):
    """Raised when a key has no object behind it."""


#: Keys are built from run ids and artifact names, and artifact names are chosen
#: by a model. Anything outside this set is rejected rather than sanitised, so a
#: bad name fails loudly instead of silently landing somewhere unintended.
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def build_key(*segments: str) -> str:
    """Join path segments into a storage key, rejecting anything unsafe.

    Artifact names come from model output, so traversal ("../"), absolute paths
    and empty segments are treated as errors. This is the only place keys are
    constructed.
    """
    if not segments:
        raise ValueError("a key needs at least one segment")
    for segment in segments:
        if not isinstance(segment, str) or not _SEGMENT.match(segment):
            raise ValueError(f"unsafe key segment: {segment!r}")
        if segment in (".", ".."):
            raise ValueError(f"unsafe key segment: {segment!r}")
    # Always "/", on every platform: these are storage keys, not OS paths.
    # Each segment is validated above, so this is plain concatenation —
    # posixpath.join's only extra behaviours (absorbing an absolute segment,
    # collapsing doubled slashes) are already impossible here.
    return "/".join(segments)


class StorageBackend(ABC):
    """Where object bytes actually live."""

    @abstractmethod
    async def put(self, key: str, data: bytes) -> str:
        """Write bytes, returning the key written."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Read bytes back. Raises ObjectNotFound if the key is absent."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove an object. Absent keys are not an error."""

    @abstractmethod
    async def list(self, prefix: str) -> list[str]:
        ...

    @abstractmethod
    async def delete_prefix(self, prefix: str) -> int:
        """Remove everything under a prefix, returning how many objects went.

        Deleting a session has to take its objects with it, and doing that key
        by key means a partial failure leaves an unreachable half. Backends
        implement it with whatever bulk primitive they have.
        """

    @abstractmethod
    async def check(self) -> None:
        """Verify the backend is reachable and writable."""
