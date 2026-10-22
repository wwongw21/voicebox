import asyncio
import threading

import pytest

from backend.services import task_queue
from backend.utils.model_lifecycle import (
    ModelResourceBusyError,
    create_model_operation_lock,
    protect_model_unload,
    run_model_thread,
)
from backend.utils.tasks import get_task_manager


class FakeModelResource:
    def __init__(self):
        self._model_operation_lock = create_model_operation_lock()
        self.unloaded = False

    @protect_model_unload
    def unload_model(self):
        self.unloaded = True


@pytest.mark.asyncio
async def test_cancel_queued_generation_skips_execution():
    task_queue.init_queue(force=True)

    running_started = asyncio.Event()
    release_running = asyncio.Event()
    queued_ran = asyncio.Event()
    later_ran = asyncio.Event()

    async def running_job():
        running_started.set()
        await release_running.wait()

    async def queued_job():
        queued_ran.set()

    async def later_job():
        later_ran.set()

    task_queue.enqueue_generation("gen-running", running_job())
    await asyncio.wait_for(running_started.wait(), timeout=1)

    task_queue.enqueue_generation("gen-queued", queued_job())
    assert task_queue.cancel_generation("gen-queued") == "queued"
    task_queue.enqueue_generation("gen-later", later_job())

    release_running.set()
    try:
        await asyncio.wait_for(task_queue._generation_queue.join(), timeout=1)
        assert not queued_ran.is_set()
        assert later_ran.is_set()
    finally:
        await task_queue.shutdown_queue()


@pytest.mark.asyncio
async def test_cancel_running_thread_keeps_queue_and_model_owned_until_completion():
    task_queue.init_queue(force=True)
    resource = FakeModelResource()
    loop = asyncio.get_running_loop()
    thread_started = threading.Event()
    release_thread = threading.Event()
    first_thread_finished = asyncio.Event()
    first_cleanup_finished = asyncio.Event()
    second_job_started = asyncio.Event()
    second_thread_finished = asyncio.Event()
    active_calls = 0
    max_active_calls = 0

    def first_inference():
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        thread_started.set()
        try:
            assert release_thread.wait(timeout=2)
            raise ValueError("model failed after cancellation")
        finally:
            active_calls -= 1
            loop.call_soon_threadsafe(first_thread_finished.set)

    def second_inference():
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        try:
            return "finished second"
        finally:
            active_calls -= 1
            loop.call_soon_threadsafe(second_thread_finished.set)

    async def first_job():
        try:
            await run_model_thread(resource._model_operation_lock, first_inference)
        except asyncio.CancelledError:
            first_cleanup_finished.set()
            raise

    async def second_job():
        second_job_started.set()
        await run_model_thread(resource._model_operation_lock, second_inference)

    task_queue.enqueue_generation("gen-running", first_job())
    assert await asyncio.wait_for(asyncio.to_thread(thread_started.wait, 2), timeout=3)
    task_queue.enqueue_generation("gen-second", second_job())

    assert task_queue.cancel_generation("gen-running") == "running"
    await asyncio.sleep(0)
    assert task_queue.cancel_generation("gen-running") == "running"

    try:
        await asyncio.sleep(0.05)
        assert "gen-running" in task_queue._running_generation_tasks
        assert not first_thread_finished.is_set()
        assert not first_cleanup_finished.is_set()
        assert not second_job_started.is_set()
        assert not second_thread_finished.is_set()
        with pytest.raises(ModelResourceBusyError):
            resource.unload_model()

        release_thread.set()
        await asyncio.wait_for(task_queue._generation_queue.join(), timeout=3)

        assert first_thread_finished.is_set()
        assert first_cleanup_finished.is_set()
        assert second_job_started.is_set()
        assert second_thread_finished.is_set()
        assert max_active_calls == 1
        resource.unload_model()
        assert resource.unloaded
    finally:
        release_thread.set()
        await task_queue.shutdown_queue()


@pytest.mark.asyncio
async def test_model_thread_lock_is_released_after_normal_and_error_completion():
    resource = FakeModelResource()

    assert await run_model_thread(resource._model_operation_lock, lambda: 42) == 42

    def fail():
        raise ValueError("model failed")

    with pytest.raises(ValueError, match="model failed"):
        await run_model_thread(resource._model_operation_lock, fail)

    resource.unload_model()
    assert resource.unloaded


@pytest.mark.asyncio
async def test_shutdown_waits_for_active_thread_and_cleans_pending_jobs(monkeypatch):
    task_queue.init_queue(force=True)
    resource = FakeModelResource()
    task_manager = get_task_manager()
    task_manager.start_generation("gen-shutdown", "profile", "running text")
    task_manager.start_generation("gen-pending", "profile", "queued text")
    thread_started = threading.Event()
    release_thread = threading.Event()
    cleanup_finished = asyncio.Event()
    queued_job_ran = asyncio.Event()
    failed_pending_jobs = []

    async def record_pending_failure(generation_id, error):
        failed_pending_jobs.append((generation_id, error))

    monkeypatch.setattr(task_queue, "_force_fail_if_active", record_pending_failure)
    completed_generation_ids = []
    complete_generation = task_manager.complete_generation

    def record_completion(generation_id):
        completed_generation_ids.append(generation_id)
        complete_generation(generation_id)

    monkeypatch.setattr(task_manager, "complete_generation", record_completion)

    def blocking_inference():
        thread_started.set()
        assert release_thread.wait(timeout=2)

    async def running_job():
        try:
            await run_model_thread(resource._model_operation_lock, blocking_inference)
        except asyncio.CancelledError:
            cleanup_finished.set()
            task_manager.complete_generation("gen-shutdown")
            raise

    async def queued_job():
        queued_job_ran.set()

    task_queue.enqueue_generation("gen-shutdown", running_job())
    assert await asyncio.wait_for(asyncio.to_thread(thread_started.wait, 2), timeout=3)
    task_queue.enqueue_generation("gen-pending", queued_job())

    shutdown = asyncio.create_task(task_queue.shutdown_queue())
    try:
        await asyncio.sleep(0.05)
        assert not shutdown.done()
        assert not cleanup_finished.is_set()
        with pytest.raises(ModelResourceBusyError):
            resource.unload_model()

        release_thread.set()
        await asyncio.wait_for(shutdown, timeout=3)
        assert cleanup_finished.is_set()
        assert not queued_job_ran.is_set()
        assert failed_pending_jobs == [
            ("gen-pending", "Server shut down before generation started")
        ]
        assert sorted(completed_generation_ids) == ["gen-pending", "gen-shutdown"]
        assert not task_manager.is_generation_active("gen-pending")
        assert not task_manager.is_generation_active("gen-shutdown")
        resource.unload_model()
        assert resource.unloaded
    finally:
        release_thread.set()
        if not shutdown.done():
            await shutdown
        task_manager.complete_generation("gen-pending")
        task_manager.complete_generation("gen-shutdown")


@pytest.mark.asyncio
async def test_shutdown_recovers_job_cancelled_before_its_coroutine_starts(monkeypatch):
    task_queue.init_queue(force=True)
    generation_id = "gen-cancelled-before-start"
    task_manager = get_task_manager()
    task_manager.start_generation(generation_id, "profile", "text")
    coroutine_started = asyncio.Event()
    failure_recovery = []

    async def record_failure(generation_id, error):
        failure_recovery.append((generation_id, error))

    async def job():
        coroutine_started.set()

    monkeypatch.setattr(task_queue, "_force_fail_if_active", record_failure)
    task_queue.enqueue_generation(generation_id, job())

    shutdown = asyncio.create_task(task_queue.shutdown_queue())
    try:
        await asyncio.wait_for(shutdown, timeout=1)

        assert not coroutine_started.is_set()
        assert failure_recovery == [
            (generation_id, "Server shut down before generation started")
        ]
        assert not task_manager.is_generation_active(generation_id)
        assert generation_id not in task_queue._running_generation_tasks
        assert generation_id not in task_queue._queued_generation_ids
        assert task_queue._generation_queue._unfinished_tasks == 0
    finally:
        task_manager.complete_generation(generation_id)
        if not shutdown.done():
            await shutdown
