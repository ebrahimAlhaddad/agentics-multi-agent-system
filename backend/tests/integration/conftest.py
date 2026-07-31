"""Integration tests: real Postgres and real object storage.

These are the checks that cannot be faked — that tables actually get created,
that bytes actually land in the object store while only a pointer lands in the
database, and that a run walks its graph to completion.

They skip cleanly when Postgres is not running, so `pytest` stays a one-command
operation. To run them:

    docker compose up -d postgres
    cd backend && pipenv run pytest
"""

import socket
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from settings import settings


def _postgres_reachable() -> bool:
    try:
        with socket.create_connection(
            (settings.POSTGRES_HOST, int(settings.POSTGRES_PORT)), timeout=1
        ):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    if _postgres_reachable():
        return
    skip = pytest.mark.skip(
        reason="Postgres not reachable — start it with `docker compose up -d postgres`"
    )
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)


@pytest_asyncio.fixture
async def postgres():
    from external.postgres import postgres_service

    await postgres_service.startup()
    yield postgres_service
    await postgres_service.shutdown()


@pytest_asyncio.fixture
async def storage(tmp_path, monkeypatch):
    """Storage rooted in tmp_path, so tests never touch a shared directory."""
    from external.storage import storage_service

    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path / "objects"))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    await storage_service.startup()
    await storage_service.sanity_check()
    yield storage_service
    await storage_service.shutdown()


@pytest_asyncio.fixture
async def session_id(postgres):
    """A real sessions row, since runs carry a foreign key to one."""
    sid = uuid.uuid4()
    async with postgres.engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO sessions (user_id, session_id) VALUES (:u, :s)"),
            {"u": f"itest-{sid}", "s": sid},
        )
    return sid
