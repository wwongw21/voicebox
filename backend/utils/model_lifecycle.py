"""Keep threaded model operations owned until their worker thread exits."""

from __future__ import annotations

import asyncio
from functools import wraps
from threading import Lock
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")


class ModelResourceBusyError(RuntimeError):
    """Raised when a model cannot be unloaded while an operation is using it."""


class _OperationReservation:
    def __init__(self):
        self.started = False
        self.cancelled = False
        self.finished = False


class ModelOperationLock:
    """Serialize model calls and account for operations before their threads start."""

    def __init__(self):
        self._operation_lock = Lock()
        self._state_lock = Lock()
        self._pending = 0
        self._unloading = False

    def reserve(self) -> _OperationReservation:
        with self._state_lock:
            if self._unloading:
                raise ModelResourceBusyError("Model is being unloaded")
            self._pending += 1
        return _OperationReservation()

    def run(
        self,
        reservation: _OperationReservation,
        function: Callable[..., _T],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> _T | None:
        with self._operation_lock:
            with self._state_lock:
                if reservation.cancelled:
                    return None
                reservation.started = True
            try:
                return function(*args, **kwargs)
            finally:
                self._finish(reservation)

    def cancel_if_not_started(self, reservation: _OperationReservation) -> None:
        with self._state_lock:
            if reservation.started or reservation.finished:
                return
            reservation.cancelled = True
            reservation.finished = True
            self._pending -= 1

    def _finish(self, reservation: _OperationReservation) -> None:
        with self._state_lock:
            if not reservation.finished:
                reservation.finished = True
                self._pending -= 1

    def begin_unload(self) -> bool:
        with self._state_lock:
            if self._unloading or self._pending:
                return False
            self._unloading = True
            return True

    def end_unload(self) -> None:
        with self._state_lock:
            self._unloading = False


def create_model_operation_lock() -> ModelOperationLock:
    """Create the lock shared by one backend's load, inference, and unload work."""
    return ModelOperationLock()


async def run_model_thread(
    lock: ModelOperationLock,
    function: Callable[..., _T],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    """Run blocking model work and retain ownership through cancellation.

    Cancelling the asyncio task cannot stop a function already running in a
    thread. Shield the worker task and, after cancellation, wait for it to end
    before propagating cancellation to the caller.
    """

    reservation = lock.reserve()

    def run_with_lock() -> _T | None:
        return lock.run(reservation, function, args, kwargs)

    try:
        worker = asyncio.create_task(asyncio.to_thread(run_with_lock))
    except BaseException:
        lock.cancel_if_not_started(reservation)
        raise
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # A second cancel request must not cancel the shielded worker or let
        # the caller release the backend while its thread still uses it.
        lock.cancel_if_not_started(reservation)
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break

        # Retrieve any exception raised after cancellation so it is not left
        # as an unobserved task exception. Cancellation remains the outcome.
        try:
            worker.result()
        except BaseException:
            pass
        raise


def protect_model_unload(function: Callable[..., _T]) -> Callable[..., _T]:
    """Reject unload while a worker thread owns the backend's model lock."""

    @wraps(function)
    def unload(self: Any, *args: Any, **kwargs: Any) -> _T:
        lock = self._model_operation_lock
        if not lock.begin_unload():
            raise ModelResourceBusyError(
                "Model is busy with an active load, prompt, or inference operation"
            )
        try:
            return function(self, *args, **kwargs)
        finally:
            lock.end_unload()

    return unload
