"""
Serial generation queue — ensures only one TTS inference runs at a time
to avoid GPU contention.
"""

import asyncio
import traceback
from dataclasses import dataclass
from typing import Coroutine, Literal

# Keep references to fire-and-forget background tasks to prevent GC
_background_tasks: set = set()


@dataclass
class GenerationJob:
    """Queued generation work plus the generation ID it belongs to."""

    generation_id: str
    coro: Coroutine
    started: bool = False


# Generation queue — serializes TTS inference to avoid GPU contention
_generation_queue: asyncio.Queue = None  # type: ignore  # initialized at startup
_generation_worker_task: asyncio.Task | None = None
_queued_generation_ids: set[str] = set()
_running_generation_tasks: dict[str, asyncio.Task] = {}
_cancelled_generation_ids: set[str] = set()
_queue_shutting_down = False


def create_background_task(coro) -> asyncio.Task:
    """Create a background task and prevent it from being garbage collected."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _generation_worker():
    """Worker that processes generation tasks one at a time."""
    queue = _generation_queue
    while True:
        job = await queue.get()
        try:
            if job.generation_id in _cancelled_generation_ids:
                _cancelled_generation_ids.discard(job.generation_id)
                job.coro.close()
                continue

            async def run_job() -> None:
                job.started = True
                await job.coro

            task = asyncio.create_task(run_job())
            _running_generation_tasks[job.generation_id] = task
            _queued_generation_ids.discard(job.generation_id)
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                worker_task = asyncio.current_task()
                if worker_task is not None and worker_task.cancelling():
                    # Shutdown cancels the worker as well as its active job.
                    # Keep the queue slot until the job has completed its own
                    # cancellation cleanup, including any joined model thread.
                    task.cancel()
                    failure = await _wait_for_cancelled_task(task)
                    if not job.started:
                        await _recover_unstarted_generation(
                            job,
                            "Server shut down before generation started",
                        )
                    elif failure is not None:
                        await _force_fail_if_active(
                            job.generation_id,
                            "Worker exited without writing terminal status",
                        )
                    raise
                elif not job.started:
                    await _recover_unstarted_generation(
                        job,
                        "Generation cancelled before it started",
                    )
        except Exception:
            traceback.print_exc()
            await _force_fail_if_active(
                job.generation_id,
                "Worker exited without writing terminal status",
            )
        finally:
            _running_generation_tasks.pop(job.generation_id, None)
            _queued_generation_ids.discard(job.generation_id)
            queue.task_done()


async def _wait_for_cancelled_task(task: asyncio.Task) -> BaseException | None:
    """Wait for a cancelled task without letting repeated cancels skip cleanup."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break

    if task.cancelled():
        return None
    try:
        task.result()
    except BaseException as error:
        return error
    return None


async def _recover_unstarted_generation(job: GenerationJob, error: str) -> None:
    """Close and terminalize a job whose coroutine never entered its body."""
    job.coro.close()
    await _force_fail_if_active(job.generation_id, error)
    from ..utils.tasks import get_task_manager

    get_task_manager().complete_generation(job.generation_id)


async def _force_fail_if_active(generation_id: str, error: str) -> None:
    """Best-effort recovery — flip an active row to failed if the worker
    bailed before writing a terminal status. Catches the case where the gen
    coroutine's own status-write raised (e.g. SQLite lock contention)."""
    try:
        from ..database import Generation as DBGeneration, get_db
        from . import history

        db = next(get_db())
        try:
            gen = db.query(DBGeneration).filter_by(id=generation_id).first()
            if gen is None:
                return
            if (gen.status or "completed") not in ("loading_model", "generating"):
                return
            await history.update_generation_status(
                generation_id=generation_id,
                status="failed",
                db=db,
                error=error,
            )
        finally:
            db.close()
    except Exception:
        traceback.print_exc()


def enqueue_generation(generation_id: str, coro):
    """Add a generation coroutine to the serial queue."""
    if _generation_queue is None:
        raise RuntimeError("Generation queue has not been initialized")
    if _queue_shutting_down:
        coro.close()
        raise RuntimeError("Generation queue is shutting down")

    _queued_generation_ids.add(generation_id)
    _generation_queue.put_nowait(GenerationJob(generation_id=generation_id, coro=coro))


def cancel_generation(generation_id: str) -> Literal["queued", "running"] | None:
    """Cancel a queued or running generation if it is still active."""
    running_task = _running_generation_tasks.get(generation_id)
    if running_task is not None:
        running_task.cancel()
        return "running"

    if generation_id in _queued_generation_ids:
        _queued_generation_ids.discard(generation_id)
        _cancelled_generation_ids.add(generation_id)
        return "queued"

    return None


def init_queue(force: bool = False):
    """Initialize the generation queue and start the worker.

    Must be called once during application startup (inside a running event loop).
    """
    global _generation_queue, _generation_worker_task, _queue_shutting_down
    global _queued_generation_ids, _running_generation_tasks, _cancelled_generation_ids

    if _generation_worker_task is not None and not _generation_worker_task.done():
        if not force:
            return
        if _running_generation_tasks or (
            _generation_queue is not None and not _generation_queue.empty()
        ):
            raise RuntimeError(
                "Cannot force-reset a queue with pending work; await shutdown_queue() first"
            )
        _generation_worker_task.cancel()

    _generation_queue = asyncio.Queue()
    _queue_shutting_down = False
    _queued_generation_ids = set()
    _running_generation_tasks = {}
    _cancelled_generation_ids = set()
    _generation_worker_task = create_background_task(_generation_worker())


async def shutdown_queue() -> None:
    """Cancel active and pending work, joining active cancellation cleanup."""
    global _generation_worker_task, _queue_shutting_down

    if _generation_queue is None:
        return

    _queue_shutting_down = True
    queue = _generation_queue
    from ..utils.tasks import get_task_manager

    task_manager = get_task_manager()
    while not queue.empty():
        job = queue.get_nowait()
        _queued_generation_ids.discard(job.generation_id)
        _cancelled_generation_ids.discard(job.generation_id)
        job.coro.close()
        queue.task_done()
        await _force_fail_if_active(
            job.generation_id,
            "Server shut down before generation started",
        )
        task_manager.complete_generation(job.generation_id)

    worker = _generation_worker_task
    if worker is None or worker.done():
        _generation_worker_task = None
        return

    for task in list(_running_generation_tasks.values()):
        task.cancel()
    worker.cancel()
    failure = await _wait_for_cancelled_task(worker)
    if failure is not None:
        traceback.print_exception(failure)
    _generation_worker_task = None
