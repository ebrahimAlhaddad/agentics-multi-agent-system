import uuid
from datetime import datetime
from typing import Optional
from uuid import UUID

from exceptions.exceptions import NotFoundException, ServiceLayerException
from external.postgres import postgres_service
from logger import logger
from models.db.session import Session
from services.artifact_service import artifact_service
from services.run_service import run_service


class SessionService:
    """Workspaces. A user may have many; each owns its own artifacts and runs."""

    def __init__(self):
        self.db = postgres_service

    async def create(self, user_id: str, title: Optional[str] = None) -> Session:
        try:
            # The id is assigned here rather than left to the column default,
            # which only fires at flush — that is why the auto-name used to need
            # a second write to read it back
            session_id = uuid.uuid4()
            saved = await self.db.add(
                Session(
                    session_id=session_id,
                    user_id=user_id,
                    title=title or f"Session {str(session_id)[:8]}",
                )
            )
            logger.info(f"Created session {saved.session_id} for {user_id}")
            return saved
        except Exception as e:
            msg = f"Error creating session: {e}"
            logger.error(msg)
            raise ServiceLayerException(msg, "SessionService") from e

    async def list_for_user(self, user_id: str) -> list[Session]:
        return await self.db.get_list(
            Session,
            filters={"user_id": user_id},
            sort_by="created",
            sort_order="desc",
        )

    async def get(self, session_id: UUID, user_id: Optional[str] = None) -> Session:
        """A session, filtered by owner when one is given.

        This is the only place user ownership is decided. Everything else —
        artifacts, runs, tasks — is scoped by session id, which routes get
        from here.
        """
        # None is "not found", not an error: a route that names the thing it is
        # acting on calls this with whatever the client sent, and a missing
        # session_id should read as a 404 like a wrong one does. Passing it
        # through would reach postgres.get, which refuses a null id with a 500.
        session = (
            await self.db.get(Session, session_id) if session_id is not None else None
        )
        if session is None or (user_id is not None and session.user_id != user_id):
            raise NotFoundException(f"session {session_id} not found")
        return session

    async def delete(self, session_id: UUID, user_id: Optional[str] = None) -> int:
        """Delete a session and everything under it. Returns the object count."""
        session = await self.get(session_id, user_id)

        objects = await artifact_service.delete(session_id=session.session_id)
        runs = await run_service.delete_for_session(session.session_id)
        # By filter, not by instance: the object handed in is detached
        await self.db.delete_list(Session, {"session_id": session.session_id})

        logger.info(
            f"Deleted session {session.session_id}: {runs} run(s), {objects} object(s)"
        )
        return objects

    async def touch(self, session: Session) -> Session:
        return await self.db.update(Session, session, {"last_activity": datetime.now()})

    async def get_or_create(
        self, user_id: str, session_id: Optional[UUID] = None
    ) -> Session:
        """The session a request should act on.

        An explicit id wins. Otherwise the most recent, creating one if the user
        has none — so a first-time caller never has to create a session before
        doing anything.
        """
        try:
            if session_id is not None:
                return await self.get(session_id, user_id)
        except NotFoundException:
            pass

        existing = await self.list_for_user(user_id)
        return existing[0] if existing else await self.create(user_id)


session_service = SessionService()
