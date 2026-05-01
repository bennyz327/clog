from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


BACKGROUND_JOB_QUEUED = "queued"
BACKGROUND_JOB_RUNNING = "running"
BACKGROUND_JOB_SUCCEEDED = "succeeded"
BACKGROUND_JOB_FAILED = "failed"
BACKGROUND_JOB_CANCELLED = "cancelled"

BACKGROUND_JOB_TERMINAL_STATUSES = (
    BACKGROUND_JOB_SUCCEEDED,
    BACKGROUND_JOB_FAILED,
    BACKGROUND_JOB_CANCELLED,
)

DEFAULT_SLOT_LIMITS: dict[str, int] = {
    "gdl": 1,
    "ffmpeg": 1,
    "http": 2,
}


class RetryableBackgroundError(Exception):
    pass


class BackgroundCancelledError(Exception):
    pass


@dataclass(frozen=True)
class JobStatusSnapshot:
    job_id: int
    job_type: str
    title: str
    state: str
    status_text: str
    progress_current: int | None
    progress_total: int | None
    can_cancel: bool
    can_terminate: bool
    created_at: float
    started_at: float | None
    finished_at: float | None
    error: str | None


class LiveJobStatus:
    """Thread-safe live job state shown by the GUI while a job is running."""

    def __init__(
        self,
        job_id: int,
        job_type: str,
        title: str,
        *,
        can_cancel: bool,
        can_terminate: bool,
    ) -> None:
        self.job_id = int(job_id)
        self.job_type = job_type
        self.title = title
        self.can_cancel = can_cancel
        self.can_terminate = can_terminate
        self._state = BACKGROUND_JOB_QUEUED
        self._status_text = ""
        self._progress_current: int | None = None
        self._progress_total: int | None = None
        self._error: str | None = None
        self._created_at = time.time()
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._cancel_requested = False
        self._terminate_requested = False
        self._terminate_callable: Callable[[], None] | None = None
        self._lock = threading.Lock()

    def mark_started(self, text: str | None = None) -> None:
        with self._lock:
            self._state = BACKGROUND_JOB_RUNNING
            self._started_at = time.time()
            if text is not None:
                self._status_text = text

    def set_status_text(self, text: str) -> None:
        with self._lock:
            self._status_text = str(text)

    def set_progress(self, current: int | None, total: int | None) -> None:
        with self._lock:
            self._progress_current = None if current is None else int(current)
            self._progress_total = None if total is None else int(total)

    def set_error(self, message: str | None) -> None:
        with self._lock:
            self._error = None if message is None else str(message)

    def set_terminate_callable(self, callback: Callable[[], None] | None) -> None:
        with self._lock:
            self._terminate_callable = callback

    def request_cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True

    def request_terminate(self) -> None:
        callback: Callable[[], None] | None
        with self._lock:
            self._cancel_requested = True
            self._terminate_requested = True
            callback = self._terminate_callable
        if callback is not None:
            callback()

    def cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel_requested

    def terminate_requested(self) -> bool:
        with self._lock:
            return self._terminate_requested

    def finish(self, state: str, *, text: str | None = None, error: str | None = None) -> None:
        with self._lock:
            self._state = state
            self._finished_at = time.time()
            if text is not None:
                self._status_text = text
            if error is not None:
                self._error = error
            self._terminate_callable = None
            self.can_cancel = False
            self.can_terminate = False

    def snapshot(self) -> JobStatusSnapshot:
        with self._lock:
            return JobStatusSnapshot(
                job_id=self.job_id,
                job_type=self.job_type,
                title=self.title,
                state=self._state,
                status_text=self._status_text,
                progress_current=self._progress_current,
                progress_total=self._progress_total,
                can_cancel=self.can_cancel,
                can_terminate=self.can_terminate,
                created_at=self._created_at,
                started_at=self._started_at,
                finished_at=self._finished_at,
                error=self._error,
            )


@dataclass(frozen=True)
class BackgroundJobHandler:
    job_type: str
    title_builder: Callable[[dict[str, Any]], str]
    slot_key: str
    supports_terminate: bool
    max_attempts: int
    timeout_seconds: int


def progress_text(current: int | None, total: int | None) -> str:
    if current is None or total is None or total <= 0:
        return "—"
    return f"{current}/{total}"
