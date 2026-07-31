"""Tests for the storage seam.

The local backend is exercised for real against tmp_path; the S3 backend is not
covered here since it needs AWS. Key validation is shared by both, so the
security-relevant surface is fully tested regardless of backend.
"""

import pytest

from external.storage.base import ObjectNotFound, build_key
from external.storage.local import LocalStorageBackend


@pytest.fixture
def store(tmp_path):
    return LocalStorageBackend(str(tmp_path / "objects"))


# ------------------------------------------------------------------ build_key


def test_build_key_joins_segments():
    assert build_key("run_8f2a", "cohorts.parquet") == "run_8f2a/cohorts.parquet"


@pytest.mark.parametrize("bad", [
    "../etc/passwd",       # traversal
    "..",                  # traversal
    ".",                   # current dir
    "/absolute",           # absolute path
    "nested/segment",      # separator smuggled into one segment
    "",                    # empty
    "  ",                  # whitespace
    ".hidden",             # must not start with a dot
    "semi;colon",
    "pipe|char",
    "back\\slash",
])
def test_build_key_rejects_unsafe_segments(bad):
    """Artifact names come from model output, so these must fail loudly."""
    with pytest.raises(ValueError):
        build_key("run_8f2a", bad)


def test_build_key_requires_at_least_one_segment():
    with pytest.raises(ValueError):
        build_key()


def test_build_key_allows_normal_artifact_names():
    for name in ("cohorts", "mrr_monthly.parquet", "chart-1.json", "a.b.c"):
        assert build_key("run_1", name).endswith(name)


# ------------------------------------------------------------ local round-trip


@pytest.mark.asyncio
async def test_put_then_get_round_trips(store):
    key = build_key("run_1", "cohorts.parquet")
    await store.put(key, b"\x00binary\xff")
    assert await store.get(key) == b"\x00binary\xff"


@pytest.mark.asyncio
async def test_get_missing_key_raises(store):
    with pytest.raises(ObjectNotFound):
        await store.get("run_1/nope")


@pytest.mark.asyncio
async def test_exists(store):
    key = build_key("run_1", "a")
    assert not await store.exists(key)
    await store.put(key, b"x")
    assert await store.exists(key)


@pytest.mark.asyncio
async def test_put_overwrites(store):
    key = build_key("run_1", "a")
    await store.put(key, b"first")
    await store.put(key, b"second")
    assert await store.get(key) == b"second"


@pytest.mark.asyncio
async def test_delete_is_idempotent(store):
    key = build_key("run_1", "a")
    await store.put(key, b"x")
    await store.delete(key)
    await store.delete(key)  # absent keys are not an error
    assert not await store.exists(key)


@pytest.mark.asyncio
async def test_empty_object_round_trips(store):
    key = build_key("run_1", "empty")
    await store.put(key, b"")
    assert await store.get(key) == b""
    assert await store.exists(key)


@pytest.mark.asyncio
async def test_list_is_scoped_to_prefix(store):
    await store.put(build_key("run_1", "a"), b"1")
    await store.put(build_key("run_1", "b"), b"2")
    await store.put(build_key("run_2", "c"), b"3")
    assert await store.list("run_1") == ["run_1/a", "run_1/b"]
    assert await store.list("run_2") == ["run_2/c"]


@pytest.mark.asyncio
async def test_list_unknown_prefix_is_empty(store):
    assert await store.list("run_nope") == []


@pytest.mark.asyncio
async def test_keys_stay_under_the_root(store):
    """The backend no longer re-checks for traversal, so build_key is the only
    thing standing between a model-chosen artifact name and the filesystem.
    That makes its rejection tests above load-bearing, not belt-and-braces."""
    await store.put(build_key("run_1", "a"), b"x")
    assert (store.root / "run_1" / "a").is_file()


@pytest.mark.asyncio
async def test_check_creates_the_root(store):
    assert not store.root.exists()
    await store.check()
    assert store.root.is_dir()


@pytest.mark.asyncio
async def test_check_reports_an_unwritable_root(tmp_path):
    """The failure wrapping moved out of the service and into the backend, so
    this is where a read-only volume has to be reported."""
    from exceptions.exceptions import ExternalServiceException

    root = tmp_path / "readonly" / "objects"
    root.parent.mkdir()
    root.parent.chmod(0o500)
    try:
        with pytest.raises(ExternalServiceException, match="not writable"):
            await LocalStorageBackend(str(root)).check()
    finally:
        root.parent.chmod(0o700)
