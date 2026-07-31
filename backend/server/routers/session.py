from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from external.cognito import cognito_service
from models.session import SessionCreate, SessionResponse
from services.session_service import session_service
from exceptions.exceptions import NotFoundException

router = APIRouter(prefix="/sessions", tags=["sessions"])


def get_auth_service():
    return cognito_service


def get_session_service():
    return session_service


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    request: Request,
    auth_service=Depends(get_auth_service),
    sessions=Depends(get_session_service),
):
    """A user's workspaces, newest first. Creates one if they have none, so a
    first-time caller never has to."""
    user = await auth_service.get_user(request)
    found = await sessions.list_for_user(user.Username)
    if not found:
        found = [await sessions.create(user.Username)]
    return found


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    body: SessionCreate = Body(default=SessionCreate()),
    auth_service=Depends(get_auth_service),
    sessions=Depends(get_session_service),
):
    user = await auth_service.get_user(request)
    return await sessions.create(user.Username, body.title)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: UUID,
    request: Request,
    auth_service=Depends(get_auth_service),
    sessions=Depends(get_session_service),
):
    """Delete a session, its runs and artifacts, and every object they own."""
    user = await auth_service.get_user(request)
    try:
        await sessions.delete(session_id, user.Username)
    except NotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
