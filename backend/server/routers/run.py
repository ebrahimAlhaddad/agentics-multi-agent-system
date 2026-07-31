import json
import mimetypes
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status

from external.cognito import cognito_service
from services.artifact_service import artifact_service
from services.run_service import run_service
from services.session_service import session_service
from models.artifact import ArtifactKind, ArtifactOrigin, ArtifactSummary
from models.review import FAITHFULNESS_NOTE
from models.run import RouterRunDetail, RouterRunRequest, RouterRunSummary
from models.run import RunStatus
from exceptions.exceptions import NotFoundException
from logger import logger

router = APIRouter(prefix="/runs", tags=["runs"])


def get_auth_service():
    return cognito_service


def get_sessions():
    return session_service


def get_runs():
    return run_service


def get_artifacts():
    return artifact_service


@router.post("", response_model=RouterRunSummary, status_code=status.HTTP_201_CREATED)
async def start_run(
    request: Request,
    body: RouterRunRequest = Body(default=RouterRunRequest()),
    auth_service=Depends(get_auth_service),
    sessions=Depends(get_sessions),
    runs=Depends(get_runs),
):
    """Open a run and start talking to the planner.

    A run now begins empty and in `planning`. There is no question on this
    route: the question is the first chat message, and the plan is whatever the
    conversation arrives at.
    """
    user = await auth_service.get_user(request)
    # On this route the session arrives on the body, not the query string.
    try:
        session = (
            await sessions.get(body.session_id, user.Username)
            if body.session_id
            else await sessions.get_or_create(user.Username)
        )
    except NotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    run = await runs.create_run(session_id=session.session_id, question=None)
    # Return what set_run_status wrote, not the instance from before it. The
    # older object still says `pending`, so the response contradicted the row.
    run = await runs.set_run_status(run, RunStatus.PLANNING)
    logger.info(f"Run {run.run_id} opened for planning")
    return run


@router.get("", response_model=list[RouterRunSummary])
async def list_runs(
    request: Request,
    session_id: Optional[UUID] = None,
    auth_service=Depends(get_auth_service),
    sessions=Depends(get_sessions),
    runs=Depends(get_runs),
):
    """Past runs, newest first — what the UI needs to offer history."""
    user = await auth_service.get_user(request)
    session = await sessions.get_or_create(user.Username, session_id)
    return await runs.list_for_session(session.session_id)


@router.get("/{run_id}", response_model=RouterRunDetail)
async def get_run(
    run_id: UUID,
    request: Request,
    session_id: Optional[UUID] = None,
    auth_service=Depends(get_auth_service),
    sessions=Depends(get_sessions),
    runs=Depends(get_runs),
    artifacts=Depends(get_artifacts),
):
    """A run and its task graph, for polling and for the DAG view."""
    user = await auth_service.get_user(request)
    try:
        session = await sessions.get(session_id, user.Username)
        run = await runs.get_run(run_id, session_id=session.session_id)
    except NotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    tasks = await runs.get_tasks(run_id)

    # Load the report too, so reopening a past run shows its answer rather than
    # just its graph — and with it the faithfulness verdict, which is stored as
    # a report of its own and must not be mistaken for the answer.
    # The answer is the artifact the terminal task marked as such. Picking the
    # first thing of kind `report` instead showed an analyst's note where the
    # answer should be — any task can write text, only one writes the answer.
    report = note = None
    for a in await artifacts.list_for(run_id=run_id):
        wanted = (
            a.origin == ArtifactOrigin.TERMINAL
            or a.name.rsplit("/", 1)[-1] == FAITHFULNESS_NOTE
        )
        if not wanted:
            continue
        try:
            value = await artifacts.read(run_id=run_id, name=a.name)
        except Exception as e:
            # Log rather than swallow: a silent `pass` here is why this returned
            # None with the artifact sitting right there.
            logger.error(f"Could not load report {a.name!r} for run {run_id}: {e}")
            continue
        if a.origin == ArtifactOrigin.TERMINAL:
            report = value
        else:
            note = value

    return RouterRunDetail(
        run_id=str(run.run_id),
        status=run.status,
        question=run.question,
        error=run.error,
        report=report,
        report_note=note,
        # `tasks` is the whole graph: depends_on rides on each row, so the
        # shape and the state come back together. This used to carry a second
        # `plan` list built from the same rows — the mistake runs.plan was,
        # repeated on the wire.
        tasks=tasks,
    )


@router.get("/{run_id}/artifacts", response_model=list[ArtifactSummary])
async def list_artifacts(
    run_id: UUID,
    request: Request,
    session_id: Optional[UUID] = None,
    auth_service=Depends(get_auth_service),
    sessions=Depends(get_sessions),
    runs=Depends(get_runs),
    artifacts=Depends(get_artifacts),
):
    """What a run produced, without the contents."""
    user = await auth_service.get_user(request)
    # Resolve the run within the caller's session before reading anything
    # hanging off it. A run id is the only thing in the URL, and it is not a
    # secret.
    try:
        session = await sessions.get(session_id, user.Username)
        await runs.get_run(run_id, session_id=session.session_id)
    except NotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return await artifacts.list_for(run_id=run_id)


@router.get("/{run_id}/artifacts/{name:path}")
async def get_artifact(
    run_id: UUID,
    name: str,
    request: Request,
    session_id: Optional[UUID] = None,
    auth_service=Depends(get_auth_service),
    sessions=Depends(get_sessions),
    runs=Depends(get_runs),
    artifacts=Depends(get_artifacts),
):
    """One artifact's contents, shaped for a browser.

    Frames are truncated: this feeds a preview panel, and sending a million
    rows to render a table nobody scrolls would be the same mistake the agents
    are careful not to make.
    """
    user = await auth_service.get_user(request)
    try:
        session = await sessions.get(session_id, user.Username)
        await runs.get_run(run_id, session_id=session.session_id)
        row = await artifacts.resolve(run_id=run_id, name=name)
        value = await artifacts.read(row)
    except NotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    if row.kind == ArtifactKind.FRAME:
        return {
            "name": name,
            "kind": row.kind,
            "columns": [str(c) for c in value.columns],
            "rows": value.head(200).to_dict(orient="records"),
            "row_count": int(len(value)),
            "truncated": len(value) > 200,
        }
    if row.kind == ArtifactKind.FILE:
        # A PNG or a pickle is bytes, and bytes are not JSON. It goes back as
        # itself, typed from the extension recorded in the object key so a
        # browser renders an image rather than downloading it.
        media_type, _ = mimetypes.guess_type(row.object_key)
        return Response(
            content=value, media_type=media_type or "application/octet-stream"
        )
    return {"name": name, "kind": row.kind, "value": value}
