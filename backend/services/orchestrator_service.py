"""The orchestrator's publish side.

An advance message means "look at this run", not "task 7 finished". It carries
nothing but a run id, and whoever handles it rebuilds the whole picture from the
task rows. That is what makes a run resumable: no state rides in the message and
none is held by the handler, so a duplicate delivery costs one wasted query and
a lost one is recovered by the next event or by the sweeper.

`advance` is the other half: it reads the rows, computes the frontier through
dag_service and dispatches. It is a handler, not a loop — one decision, publish,
return. It makes no model call, so what it does is entirely determined by the
task rows.
"""

from uuid import UUID

from models.run import RunStatus, TaskStatus
from external.queue import queue_service
from models.queue_message import QueueMessage
from services.dag_service import dag_service
from services.run_service import run_service
from settings import settings
from logger import logger


ADVANCE = "advance"


class OrchestratorService:
    def __init__(self, queue=queue_service):
        self.queue = queue

    async def request_advance(self, run_id: UUID, cause: str) -> str:
        """Publish an advance for a run. Returns the message id.

        `cause` names the event that prompted this and is used only for the log
        line — it is not an identity
        """
        message_id = await self.queue.send(
            settings.QUEUE_RUNS,
            QueueMessage(handler=ADVANCE, run_id=str(run_id)).as_body(),
        )
        logger.info(f"Advance queued for run {run_id} ({cause})")
        return message_id

    async def advance(self, run_id: UUID) -> None:
        """Look at a run and do whatever it needs next. Never waits.

        Everything is rebuilt from the rows, so a duplicate delivery costs one
        wasted query and a lost one is recovered by the next event.
        """
        run = await run_service.get_run(run_id)
        if run.status != RunStatus.RUNNING:
            logger.info(f"Run {run_id} is {run.status}; nothing to advance")
            return

        tasks = await run_service.get_tasks(run_id)

        # Settle the rows before deciding anything from them. Order matters: a
        # task can only be declared dead once it is out of attempts, and its
        # descendants can only be superseded once it is.
        #
        # 1. A failure with attempts left goes back in the pool
        for i, task in enumerate(tasks):
            if (
                task.status == TaskStatus.FAILED
                and (task.attempts or 0) < settings.MAX_TASK_ATTEMPTS
            ):
                tasks[i] = await run_service.set_task_status(task, TaskStatus.REWORK)

        # 2. A task waiting to run that has no attempts left never will. Failing
        #    it here rather than at dispatch is what stops a worker and its
        #    reviewer disagreeing forever.
        for i, task in enumerate(tasks):
            if (
                task.status in TaskStatus.DISPATCHABLE
                and (task.attempts or 0) >= settings.MAX_TASK_ATTEMPTS
            ):
                tasks[i] = await run_service.set_task_status(
                    task,
                    TaskStatus.FAILED,
                    error=f"gave up after {settings.MAX_TASK_ATTEMPTS} attempts",
                )

        # 3. Whatever depended on a task that failed for good consumes something
        #    that will never exist. Without this they sit `pending` forever —
        #    not terminal, so the run never finishes either, and no further
        #    advance is coming to look at them.
        stranded = {
            dependent
            for task in tasks
            if task.status == TaskStatus.FAILED
            for dependent in dag_service.invalidated_by(task.task_id, tasks)
        }
        for i, task in enumerate(tasks):
            if task.task_id in stranded and task.status not in TaskStatus.TERMINAL:
                logger.info(f"{task.task_id} superseded: an upstream task failed")
                tasks[i] = await run_service.set_task_status(
                    task,
                    TaskStatus.SUPERSEDED,
                    error="an upstream task failed and will not be retried",
                )

        ## all tasks are terminal
        if dag_service.is_drained(tasks):
            failed = [t for t in tasks if t.status == TaskStatus.FAILED]
            if failed:
                # Say which task and why. A run that reports "failed" and
                # nothing else sends whoever reads it to the container logs.
                await run_service.set_run_status(
                    run,
                    RunStatus.FAILED,
                    error="; ".join(
                        f"{t.task_id}: {t.error or 'failed'}" for t in failed
                    ),
                )
            else:
                await run_service.set_run_status(run, RunStatus.DONE)
            logger.info(
                f"Run {run_id} finished: "
                f"{'failed ' + str([t.task_id for t in failed]) if failed else 'done'}"
            )
            return

        # An empty frontier is not a problem: either work is in flight and its
        # completion publishes the next advance
        for task in dag_service.frontier(tasks):
            # Claim before dispatching. The claim is conditional on the status
            # this advance read, so losing the race costs nothing: whoever won
            # it has already dispatched the task, and this one moves on
            if not await run_service.claim_task(task):
                logger.info(f"{task.task_id} was claimed by another advance")
                continue
            await self.queue.send(
                settings.QUEUE_TASKS,
                QueueMessage(
                    handler=task.role, run_id=str(run_id), task_id=task.task_id
                ).as_body(),
            )
            logger.info(f"Dispatched {task.task_id} ({task.role}) for run {run_id}")


orchestrator_service = OrchestratorService()
