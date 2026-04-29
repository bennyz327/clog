from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import LOGGER


@dataclass
class Job:
    name: str
    progress: float = 0.0
    message: str = ""
    done: bool = False
    error: BaseException | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class JobPool:
    """Thin wrapper around ThreadPoolExecutor for background work."""

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="clog-job")
        self._closed = False

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        if self._closed:
            raise RuntimeError("JobPool is shut down")
        return self._executor.submit(_safe_run, fn, *args, **kwargs)

    def shutdown(self, wait: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=wait)


def _safe_run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:
        LOGGER.exception("Background job failed: %s", exc)
        raise
