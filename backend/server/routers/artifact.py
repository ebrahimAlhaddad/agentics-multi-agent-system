from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from external.cognito import cognito_service
from models.artifact import ArtifactOrigin, RouterArtifactResponse
from services.artifact_service import artifact_service
from services.session_service import session_service
from exceptions.exceptions import NotFoundException, ServiceLayerException
from logger import logger

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def get_auth_service():
    return cognito_service


def get_artifact_service():
    return artifact_service


def get_session_service():
    return session_service


async def _with_profile(artifacts, artifact) -> dict:
    """An artifact row plus its profile, which is a separate row.

    The two are separate in the database because not everything has a profile;
    they are one object on the wire because the UI renders them together.
    """
    profile = await artifacts.profile(artifact.artifact_id)
    return {
        **{
            c: getattr(artifact, c)
            for c in (
                "artifact_id",
                "session_id",
                "run_id",
                "task_id",
                "name",
                "origin",
                "kind",
                "created_at",
            )
        },
        "row_count": profile.row_count if profile else None,
        "columns": profile.columns if profile else None,
    }


@router.post(
    "", response_model=RouterArtifactResponse, status_code=status.HTTP_201_CREATED
)
async def upload_artifact(
    request: Request,
    file: UploadFile = File(...),
    session_id: Optional[UUID] = None,
    auth_service=Depends(get_auth_service),
    artifacts=Depends(get_artifact_service),
    sessions=Depends(get_session_service),
):
    """Upload a CSV and get its profile back."""
    user = await auth_service.get_user(request)
    session = await sessions.get_or_create(user.Username, session_id)

    try:
        artifact = await artifacts.process_upload(
            session_id=session.session_id,
            filename=file.filename or "upload.csv",
            content=await file.read(),
        )
    except ServiceLayerException as e:
        # Bad input, not a server fault — the message names what to fix.
        logger.info(f"Rejected upload {file.filename!r}: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return await _with_profile(artifacts, artifact)


@router.get("/{artifact_id}", response_model=RouterArtifactResponse)
async def get_artifact(
    artifact_id: UUID,
    request: Request,
    session_id: Optional[UUID] = None,
    auth_service=Depends(get_auth_service),
    artifacts=Depends(get_artifact_service),
    sessions=Depends(get_session_service),
):
    user = await auth_service.get_user(request)
    try:
        session = await sessions.get(session_id, user.Username)
        artifact = await artifacts.resolve(artifact_id=artifact_id, session_id=session.session_id)
    except NotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return await _with_profile(artifacts, artifact)


@router.get("", response_model=list[RouterArtifactResponse])
async def list_artifacts(
    request: Request,
    session_id: Optional[UUID] = None,
    auth_service=Depends(get_auth_service),
    artifacts=Depends(get_artifact_service),
    sessions=Depends(get_session_service),
):
    """The session's inputs — what a run can be asked about.

    Profiles are joined in: a session holds a handful of inputs, and the row
    count is the one thing the sidebar shows next to a name.
    """
    user = await auth_service.get_user(request)
    session = await sessions.get_or_create(user.Username, session_id)
    rows = await artifacts.list_for(session_id=session.session_id, origin=ArtifactOrigin.INPUT)
    return [await _with_profile(artifacts, row) for row in rows]


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: UUID,
    request: Request,
    session_id: Optional[UUID] = None,
    auth_service=Depends(get_auth_service),
    artifacts=Depends(get_artifact_service),
    sessions=Depends(get_session_service),
):
    """Remove an upload the session no longer wants."""
    user = await auth_service.get_user(request)
    try:
        session = await sessions.get(session_id, user.Username)
        await artifacts.delete(artifact_id=artifact_id, session_id=session.session_id)
    except NotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
