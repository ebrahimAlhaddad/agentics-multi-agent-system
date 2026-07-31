import json
from typing import Optional
from uuid import UUID

from exceptions.exceptions import NotFoundException
from external.cognito import cognito_service
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from logger import logger
from models.run import RouterChatRequest, RouterRunRequest, RouterRunSummary, RunStatus
from services.artifact_service import artifact_service
from services.agents import planner
from services.orchestrator_service import orchestrator_service
from services.run_service import run_service
from services.session_service import session_service

from models.run_context import RunContext

router = APIRouter(prefix="/agents", tags=["agents"])


def get_auth_service():
    return cognito_service


def get_sessions():
    return session_service


def get_runs():
    return run_service


def get_artifacts():
    return artifact_service


def get_orchestrator():
    return orchestrator_service


async def build_context(
    request: Request,
    session_id: Optional[UUID],
    run_id: Optional[UUID],
    auth_service,
    sessions,
    runs,
) -> RunContext:
    """Authorise a request and package what the agents may act on.

    The one place a RunContext is made. Everything downstream — agents, their
    tools, the services those tools call — trusts the session on it, so this is
    where that trust is earned: user_id comes from auth, the session is checked
    against it, and the run is checked against the session.

    Nothing below re-checks, and nothing below should
    """
    user = await auth_service.get_user(request)
    session = (
        await sessions.get(session_id, user.Username)
        if run_id is not None
        else await sessions.get_or_create(user.Username, session_id)
    )
    if run_id is not None:
        await runs.get_run(run_id, session_id=session.session_id)
    return RunContext(
        user_id=user.Username, session_id=session.session_id, run_id=run_id
    )


@router.get("/{run_id}/messages")
async def get_messages(
    run_id: UUID,
    request: Request,
    session_id: Optional[UUID] = None,
    auth_service=Depends(get_auth_service),
    sessions=Depends(get_sessions),
    runs=Depends(get_runs),
):
    """What was already said on this run (transcript)"""
    try:
        await build_context(request, session_id, run_id, auth_service, sessions, runs)
    except NotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return await planner.transcript(run_id)


@router.post("/{run_id}/messages")
async def send_message(
    run_id: UUID,
    request: Request,
    body: RouterChatRequest = Body(...),
    auth_service=Depends(get_auth_service),
    sessions=Depends(get_sessions),
    runs=Depends(get_runs),
):
    """Say something to the planner and stream the reply.

    Server-sent events, one `data:` line per text delta, then `[DONE]`. History
    is the SDK session keyed by run_ids. Chat cached at client
    """
    try:
        ctx = await build_context(
            request, body.session_id, run_id, auth_service, sessions, runs
        )
    except NotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    async def events():
        try:
            async for event in planner.stream(ctx, body.content):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            logger.error(f"Run {run_id} chat failed: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post(
    "/{run_id}/approve",
    response_model=RouterRunSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def approve_run(
    run_id: UUID,
    request: Request,
    body: RouterRunRequest = Body(default=RouterRunRequest()),
    auth_service=Depends(get_auth_service),
    sessions=Depends(get_sessions),
    runs=Depends(get_runs),
    orchestrator=Depends(get_orchestrator),
):
    """Hand an approved plan to the orchestrator.

    202, not 200: this returns as soon as the run is queued, and the work
    happens in a process that is not this one. Progress comes from polling
    GET /runs/{run_id}, which already returns the task graph.

    Authorisation is done inline rather than through build_context because that
    builds a RunContext for agents and tools, and this route calls neither — it
    would also discard the run it just fetched, forcing a second read to check
    the status.
    """
    user = await auth_service.get_user(request)
    try:
        # Approving a run from outside the caller's session would spend someone
        # else's budget on work they never started.
        session = await sessions.get(body.session_id, user.Username)
        run = await runs.get_run(run_id, session_id=session.session_id)
    except NotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    if run.status != RunStatus.AWAITING_APPROVAL:
        # Not a failure of the run — a run still planning has no task graph to
        # walk, and one already running does not need approving twice.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"run is {run.status}, not {RunStatus.AWAITING_APPROVAL}",
        )

    # Status first, then publish. Crashing between them leaves a run marked
    # running with no advance in flight, which the sweeper recovers. The other
    # order delivers an advance for a run still marked awaiting_approval, which
    # the handler would have to refuse — turning a recoverable stall into a
    # dropped run.
    run = await runs.set_run_status(run, RunStatus.RUNNING)
    await orchestrator.request_advance(run_id, "approve")
    logger.info(f"Run {run_id} approved and queued")
    return run
