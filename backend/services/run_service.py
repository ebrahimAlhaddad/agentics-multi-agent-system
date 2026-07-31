from typing import Any, Optional
from uuid import UUID
from datetime import datetime

from external.postgres import postgres_service
from models.db.run import Run
from models.db.run_task import RunTask
from models.run import TaskStatus
from exceptions.exceptions import ServiceLayerException, NotFoundException
from logger import logger


class RunService:
    """Persistence for runs and their task graphs.

    This service reads and writes. It does not decide anything, and it does not
    touch artifacts — those are artifact_service's, addressed by
    `list_for(run_id=...)`.
    """

    def __init__(self):
        self.db = postgres_service

    # ------------------------------------------------------------------ runs

    async def create_run(
        self,
        session_id: UUID,
        question: Optional[str] = None,
    ) -> Run:
        try:
            run = Run(session_id=session_id, question=question)
            return await self.db.add(run)
        except Exception as e:
            msg = f"Error creating run: {e}"
            logger.error(msg)
            raise ServiceLayerException(msg, "RunService") from e

    async def get_run(
        self,
        run_id: UUID,
        raise_not_found: bool = True,
        session_id: Optional[UUID] = None,
    ) -> Optional[Run]:
        """A run by id, scoped to a session.

        Routes pass the session they already resolved. Whether that session
        belongs to the caller is session_service's question and is answered
        before this is called — this service only filters rows.

        Internal callers already on an authorised path omit it.
        """
        run = await self.db.get(Run, run_id)
        if run is not None and session_id is not None and run.session_id != session_id:
            run = None
        if run is None and raise_not_found:
            raise NotFoundException(f"Run {run_id} not found")
        return run

    async def set_run_status(
        self, run: Run, status: str, error: Optional[str] = None
    ) -> Run:
        # `error` is always written, so moving a run out of `failed` clears the
        # reason it failed. A stale one would sit under a plan that replaced it.
        return await self.db.update(
            Run,
            run,
            {
                "status": status,
                "error": error,
                "updated_at": datetime.now(),
            },
        )

    async def list_for_session(self, session_id: UUID, limit: int = 50) -> list[Run]:
        return await self.db.get_list(
            Run,
            filters={"session_id": session_id},
            limit=limit,
            sort_by="created_at",
            sort_order="desc",
        )

    # ----------------------------------------------------------------- tasks

    async def create_tasks(self, run_id: UUID, nodes: list[dict]) -> list[RunTask]:
        """Materialise a validated plan's nodes as task rows."""
        created = []
        try:
            for node in nodes:
                created.append(
                    await self.db.add(
                        RunTask(
                            run_id=run_id,
                            task_id=node["id"],
                            role=node["role"],
                            description=node["description"],
                            acceptance=node.get("acceptance"),
                            depends_on=node.get("depends_on", []),
                            produces=node.get("produces", []),
                            consumes=node.get("consumes", []),
                            columns=node.get("columns", []),
                        )
                    )
                )
            return created
        except Exception as e:
            msg = f"Error creating tasks for run {run_id}: {e}"
            logger.error(msg)
            raise ServiceLayerException(msg, "RunService") from e

    async def set_intent(self, run: Run, question: str, approach: str) -> Run:
        """What this run is for, as the planner understood it."""
        return await self.db.update(
            Run,
            run,
            {"question": question, "approach": approach, "updated_at": datetime.now()},
        )

    async def set_inputs(self, run: Run, artifact_ids: list[str]) -> Run:
        """Record which input artifacts this run works with. Replaces, never appends —
        re-planning may drop one as easily as add one."""
        return await self.db.update(
            Run, run, {"inputs": artifact_ids, "updated_at": datetime.now()}
        )

    async def replace_tasks(self, run_id: UUID, nodes: list[dict]) -> list[RunTask]:
        """Swap in a new plan.

        The planner may propose several times in one conversation, so this
        clears what was there first. Only safe before anything has run
        """
        await self.db.delete_list(RunTask, {"run_id": run_id})
        return await self.create_tasks(run_id, nodes)

    async def delete_for_session(self, session_id: UUID) -> int:
        """Delete every run a session owns, and their tasks. Returns run count."""
        runs = await self.db.get_list(Run, filters={"session_id": session_id})
        for run in runs:
            await self.db.delete_list(RunTask, {"run_id": run.run_id})
        await self.db.delete_list(Run, {"session_id": session_id})
        logger.info(f"Deleted {len(runs)} run(s) from session {session_id}")
        return len(runs)

    async def get_tasks(self, run_id: UUID) -> list[RunTask]:
        """Load a run's whole task set.

        The orchestrator calls this once per frontier round and computes topology
        in memory — the database is never asked a graph question.
        """
        return await self.db.get_list(
            # Ascending, so a rebuilt plan lists nodes in the order the planner
            # emitted them; get_list defaults to desc.
            RunTask,
            filters={"run_id": run_id},
            sort_by="created_at",
            sort_order="asc",
        )

    async def get_task(self, run_id: UUID, task_id: str) -> Optional[RunTask]:
        return await self.db.get(
            RunTask,
            is_composite=True,
            composite_keys={"run_id": run_id, "task_id": task_id},
        )

    async def set_task_status(
        self, task: RunTask, status: str, error: Optional[str] = None
    ) -> RunTask:
        updates: dict[str, Any] = {"status": status}
        if status == "running" and task.started_at is None:
            updates["started_at"] = datetime.now()
        if status in ("done", "failed"):
            updates["finished_at"] = datetime.now()
        if error is not None:
            updates["error"] = error
        return await self.db.update(RunTask, task, updates)

    async def claim_task(self, task: RunTask) -> bool:
        """Take ownership of a task for dispatch. True if this caller won it."""
        changed = await self.db.update_where(
            RunTask,
            {"run_id": task.run_id, "task_id": task.task_id, "status": task.status},
            {
                "status": TaskStatus.RUNNING,
                "started_at": task.started_at or datetime.now(),
                "attempts": (task.attempts or 0) + 1,
            },
        )
        return changed == 1

run_service = RunService()
