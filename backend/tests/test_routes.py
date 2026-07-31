"""HTTP-layer tests.

Every service dependency is overridden, so these assert what the routes
themselves do — parameter wiring, status codes, response shape — and nothing
about what the services behind them compute.

They exist because a route once crashed with `NameError: name 'session_id' is
not defined` and no test noticed: everything else exercises the services
directly, so the HTTP layer was reachable only by clicking. The browser reported
that 500 as "load failed", which looks like a network fault and sent the
debugging in the wrong direction twice.
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers import agent as agent_router
from server.routers import artifact as artifact_router
from server.routers import run as run_router
from server.routers import session as session_router
from exceptions.exceptions import NotFoundException

RUN_ID = str(uuid.uuid4())
ARTIFACT_ID = str(uuid.uuid4())
SESSION_ID = uuid.uuid4()


# ------------------------------------------------------------------- doubles


@dataclass
class FakeUser:
    Username: str = "tester"


class FakeAuth:
    async def get_user(self, request):
        return FakeUser()


@dataclass
class FakeSession:
    session_id: uuid.UUID = SESSION_ID
    user_id: str = "tester"
    title: Optional[str] = "Session one"
    created: Optional[object] = None


class FakeSessions:
    def __init__(self):
        self.asked_for: list = []
        self.missing = False

    async def get_or_create(self, user_id, session_id=None):
        self.asked_for.append(session_id)
        return FakeSession()

    async def get(self, session_id, user_id=None):
        # The identity form: routes that name the object they act on call this,
        # so a wrong or missing session is a 404 rather than a quiet fallback.
        self.asked_for.append(session_id)
        if self.missing:
            raise NotFoundException(f"session {session_id} not found")
        return FakeSession()

    async def list_for_user(self, user_id):
        return [FakeSession()]

    async def create(self, user_id, title=None):
        return FakeSession(session_id=uuid.uuid4(), title=title or "Session new")


@dataclass
class FakeRun:
    run_id: uuid.UUID = uuid.UUID(RUN_ID)
    session_id: uuid.UUID = SESSION_ID
    status: str = "done"
    question: str = "why?"
    error: Optional[str] = None
    inputs: list = field(default_factory=lambda: [ARTIFACT_ID])
    created_at: Optional[object] = None


@dataclass
class FakeTask:
    task_id: str = "n1"
    role: str = "analyst"
    description: str = "count the rows"
    status: str = "done"
    attempts: int = 1
    depends_on: list = field(default_factory=list)
    produces: list = field(default_factory=lambda: ["counts"])
    consumes: list = field(default_factory=list)
    error: Optional[str] = None


class FakeRuns:
    def __init__(self):
        self.missing = False
        self.foreign = False          # belongs to another user
        self.checked_for: list = []   # session each lookup was scoped to
        self.created: list = []

    async def create_run(self, session_id, question=None):
        self.created.append(session_id)
        return FakeRun(status="pending")

    async def set_run_status(self, run, status):
        return FakeRun(status=status)

    async def list_for_session(self, session_id):
        return [FakeRun()]

    async def get_run(self, run_id, raise_not_found=True, session_id=None):
        self.checked_for.append(session_id)
        if self.missing:
            raise NotFoundException("no such run")
        if self.foreign and session_id is not None:
            # A run outside the caller's session is indistinguishable from one
            # that does not exist — NotFound, never Forbidden.
            raise NotFoundException(f"Run {run_id} not found")
        return FakeRun()

    async def get_tasks(self, run_id):
        return [FakeTask()]

    @staticmethod
    def as_node(task):
        return {"id": task.task_id, "role": task.role, "depends_on": list(task.depends_on)}


@dataclass
class FakeArtifactRow:
    artifact_id: uuid.UUID = uuid.UUID(ARTIFACT_ID)
    session_id: uuid.UUID = SESSION_ID
    run_id: Optional[uuid.UUID] = uuid.UUID(RUN_ID)
    task_id: Optional[str] = "n1"
    name: str = "n1/summary"
    origin: str = "transient"
    kind: str = "report"
    object_key: str = "s/a.md"
    created_at: Optional[object] = None


@dataclass
class FakeProfile:
    row_count: Optional[int] = 12
    columns: Optional[list] = field(default_factory=lambda: [{"name": "a"}])


class FakeArtifacts:
    """Holds one report and one frame, the two shapes the route branches on."""

    def __init__(self):
        self.rows = [
            FakeArtifactRow(),
            FakeArtifactRow(name="n1/table", kind="frame", artifact_id=uuid.uuid4()),
            FakeArtifactRow(
                name="n1/plot", kind="file", artifact_id=uuid.uuid4(),
                object_key="s/a.png",
            ),
        ]
        self.uploaded: list = []
        self.foreign = False
        self.checked_for: list = []

    async def list_for(self, **filters):
        if filters.get("origin") == "input":
            return [FakeArtifactRow(name="input/subs", origin="input", kind="frame")]
        return self.rows

    async def _decode(self, artifact):
        if artifact.name == "n1/table":
            return pd.DataFrame({"a": range(500)})
        if artifact.name == "n1/plot":
            return b"\x89PNG\r\n\x1a\n pixels"
        return "the written report"

    async def read(self, artifact=None, **locator):
        if artifact is None:
            artifact = await self.resolve(**locator)
        return await self._decode(artifact)

    # session-scoped
    async def process_upload(self, session_id, filename, content):
        self.uploaded.append((session_id, filename, len(content)))
        return FakeArtifactRow(
            name=f"input/{filename.rsplit('.', 1)[0]}", origin="input",
            kind="frame", run_id=None, task_id=None,
        )

    async def resolve(
        self, *, artifact_id=None, run_id=None, session_id=None,
        name=None, required=True,
    ):
        if name is not None:
            for row in self.rows:
                if row.name == name:
                    return row
            raise NotFoundException(f"no artifact {name}")
        self.checked_for.append(session_id)
        if self.foreign and session_id is not None:
            raise NotFoundException(f"artifact {artifact_id} not found")
        return FakeArtifactRow()

    async def profile(self, artifact_id):
        return FakeProfile()


@pytest.fixture
def client(monkeypatch):
    """An app with only the routers, and every dependency overridden."""
    app = FastAPI()
    app.include_router(run_router.router)
    app.include_router(artifact_router.router)
    app.include_router(agent_router.router)
    app.include_router(session_router.router)

    sessions = FakeSessions()
    runs, artifacts = FakeRuns(), FakeArtifacts()
    app.dependency_overrides[run_router.get_auth_service] = lambda: FakeAuth()
    app.dependency_overrides[run_router.get_sessions] = lambda: sessions
    app.dependency_overrides[run_router.get_runs] = lambda: runs
    app.dependency_overrides[run_router.get_artifacts] = lambda: artifacts
    app.dependency_overrides[agent_router.get_auth_service] = lambda: FakeAuth()
    app.dependency_overrides[agent_router.get_sessions] = lambda: sessions
    app.dependency_overrides[agent_router.get_runs] = lambda: runs
    app.dependency_overrides[artifact_router.get_auth_service] = lambda: FakeAuth()
    app.dependency_overrides[artifact_router.get_artifact_service] = lambda: artifacts
    app.dependency_overrides[artifact_router.get_session_service] = lambda: sessions
    app.dependency_overrides[session_router.get_auth_service] = lambda: FakeAuth()
    app.dependency_overrides[session_router.get_session_service] = lambda: sessions

    async def transcript(run_id):
        return [
            {"role": "user", "content": "why is revenue flat?"},
            {"role": "assistant", "content": "Let me look at the data."},
        ]

    monkeypatch.setattr(agent_router.planner, "transcript", transcript)

    c = TestClient(app)
    c.sessions, c.runs, c.artifacts = sessions, runs, artifacts
    return c


# ----------------------------------------------------------------- POST /runs


def test_opening_a_run_takes_no_question(client):
    """A run starts empty and in planning — the question comes from the chat."""
    r = client.post("/runs", json={})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "planning"


def test_session_id_on_the_body_reaches_the_resolver(client):
    """It arrives on the body here and on the query string elsewhere; mixing the
    two is what broke this route."""
    sid = str(uuid.uuid4())
    client.post("/runs", json={"session_id": sid})
    assert str(client.sessions.asked_for[-1]) == sid


def test_omitting_the_session_falls_back(client):
    client.post("/runs", json={})
    assert client.sessions.asked_for[-1] is None


def test_a_malformed_run_id_is_rejected(client):
    assert client.get("/runs/not-a-uuid").status_code == 422


# ------------------------------------------------------------- read paths
# The UI polls these on a timer, so a break here is what surfaces as a page
# that silently stops updating.


def test_listing_runs_is_scoped_to_a_session(client):
    sid = str(uuid.uuid4())
    r = client.get(f"/runs?session_id={sid}")
    assert r.status_code == 200
    assert str(client.sessions.asked_for[-1]) == sid


def test_a_run_carries_its_tasks_and_report(client):
    r = client.get(f"/runs/{RUN_ID}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done"
    # the graph is the task rows — depends_on rides on each one, so there is
    # no second plan list to disagree with them
    assert body["tasks"][0]["depends_on"] == []
    assert body["tasks"][0]["task_id"] == "n1"


def test_an_unknown_run_is_a_404_not_a_500(client):
    client.runs.missing = True
    assert client.get(f"/runs/{RUN_ID}").status_code == 404


def test_listing_artifacts_omits_contents(client):
    r = client.get(f"/runs/{RUN_ID}/artifacts")
    assert r.status_code == 200
    names = {a["name"] for a in r.json()}
    assert names == {"n1/summary", "n1/table", "n1/plot"}
    assert "value" not in r.json()[0]


def test_fetching_a_report_artifact(client):
    r = client.get(f"/runs/{RUN_ID}/artifacts/n1/summary")
    assert r.json() == {
        "name": "n1/summary", "kind": "report", "value": "the written report"
    }


def test_a_frame_artifact_is_truncated(client):
    """500 rows in, 200 out — this feeds a preview panel, not a download."""
    body = client.get(f"/runs/{RUN_ID}/artifacts/n1/table").json()
    assert body["row_count"] == 500
    assert len(body["rows"]) == 200
    assert body["truncated"] is True
    assert body["columns"] == ["a"]


def test_a_binary_artifact_comes_back_as_itself(client):
    """A PNG is not JSON — it goes back typed, so a browser renders it."""
    r = client.get(f"/runs/{RUN_ID}/artifacts/n1/plot")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content.startswith(b"\x89PNG")


def test_an_unknown_artifact_is_a_404(client):
    assert client.get(f"/runs/{RUN_ID}/artifacts/n1/nope").status_code == 404


# --------------------------------------------------------------- /artifacts


def test_uploading_a_csv(client):
    r = client.post("/artifacts", files={"file": ("subs.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 201, r.text
    # Qualified on the way in, so the planner sees one namespace.
    assert r.json()["name"] == "input/subs"
    assert client.artifacts.uploaded[-1][1] == "subs.csv"


def test_an_upload_carries_its_profile(client):
    r = client.post("/artifacts", files={"file": ("subs.csv", b"a,b\n1,2\n", "text/csv")})
    body = r.json()
    # Two rows in the database, one object on the wire.
    assert body["row_count"] == 12
    assert body["columns"] == [{"name": "a"}]


def test_upload_without_a_file_is_rejected(client):
    assert client.post("/artifacts").status_code == 422


def test_listing_artifacts_is_scoped_to_a_session(client):
    sid = str(uuid.uuid4())
    client.get(f"/artifacts?session_id={sid}")
    assert str(client.sessions.asked_for[-1]) == sid


# --------------------------------------------------------------- /sessions


def test_listing_sessions(client):
    r = client.get("/sessions")
    assert r.status_code == 200
    assert r.json()[0]["title"] == "Session one"


def test_creating_a_session_auto_names_it(client):
    r = client.post("/sessions", json={})
    assert r.status_code == 201
    assert r.json()["title"] == "Session new"


def test_creating_a_named_session(client):
    assert client.post("/sessions", json={"title": "Q3"}).json()["title"] == "Q3"


# ------------------------------------------------------------- authorisation
# Every one of these routes takes an id straight off the URL. Authentication
# alone only proves you are *a* user; without an ownership check any logged-in
# caller could read another user's data by guessing a uuid.


@pytest.mark.parametrize("path", [
    f"/runs/{RUN_ID}",
    f"/runs/{RUN_ID}/artifacts",
    f"/runs/{RUN_ID}/artifacts/n1/summary",
])
def test_reads_are_scoped_to_the_owner(client, path):
    client.runs.foreign = True
    assert client.get(path).status_code == 404


def test_another_users_artifact_is_not_readable(client):
    client.artifacts.foreign = True
    assert client.get(f"/artifacts/{ARTIFACT_ID}").status_code == 404


def test_lookups_are_scoped_to_the_resolved_session(client):
    """The scope comes from the session resolved for the authenticated user, so
    the ownership question is answered once, in session_service."""
    client.get(f"/runs/{RUN_ID}")
    assert client.runs.checked_for[-1] == SESSION_ID


def test_a_foreign_run_is_a_404_not_a_403(client):
    """403 would confirm the id exists, which is what a prober is after."""
    client.runs.foreign = True
    assert client.get(f"/runs/{RUN_ID}").status_code == 404


# ------------------------------------------------------------------ /agents


def test_reopening_a_run_returns_what_was_said(client):
    """A reopened run showed its graph and an empty chat, which read as if the
    planner had forgotten. It had not — the transcript is stored per run."""
    r = client.get(f"/agents/{RUN_ID}/messages?session_id={SESSION_ID}")
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"role": "user", "content": "why is revenue flat?"},
        {"role": "assistant", "content": "Let me look at the data."},
    ]


def test_another_users_transcript_is_not_readable(client):
    client.runs.foreign = True
    assert client.get(f"/agents/{RUN_ID}/messages").status_code == 404
