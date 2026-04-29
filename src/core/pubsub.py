from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Event, Lock
from typing import Any, Callable
import weakref

from .config import LOGGER


@dataclass(frozen=True)
class _PendingPub:
    topic: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class PubSub:
    """Hydrus-style queued topic bus.

    `pub()` enqueues work to be processed later by the GUI event loop, while
    `pubimmediate()` invokes subscribers synchronously.
    """

    def __init__(self, valid_callable: Callable[[Any], bool] | None = None) -> None:
        self._valid_callable = valid_callable or (lambda obj: True)
        self._topics_to_objects: dict[str, weakref.WeakSet[Any]] = {}
        self._topics_to_method_names: dict[str, set[str]] = defaultdict(set)
        self._pubs: list[_PendingPub] = []
        self._lock = Lock()
        self._received_job_event = Event()
        self._notifier: Callable[[], None] | None = None

    def _get_callable_tuples(self, topic: str) -> list[tuple[Any, Callable[..., Any]]]:
        callable_tuples: list[tuple[Any, Callable[..., Any]]] = []
        objects = self._topics_to_objects.get(topic)
        if not objects:
            return callable_tuples

        method_names = self._topics_to_method_names.get(topic, set())
        for obj in list(objects):
            try:
                if obj is None or not self._valid_callable(obj):
                    continue
                for method_name in method_names:
                    if hasattr(obj, method_name):
                        callable_tuples.append((obj, getattr(obj, method_name)))
            except Exception:
                continue
        return callable_tuples

    def process(self) -> None:
        with self._lock:
            if not self._pubs:
                return
            pubs = self._pubs
            self._pubs = []

        for pending in pubs:
            try:
                callable_tuples = self._get_callable_tuples(pending.topic)
                for _obj, fn in callable_tuples:
                    fn(*pending.args, **pending.kwargs)
            except Exception as exc:
                LOGGER.exception("PubSub processing failed for %s: %s", pending.topic, exc)

    def pub(self, topic: str, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self._pubs.append(_PendingPub(topic, args, kwargs))
        self._received_job_event.set()
        if self._notifier is not None:
            self._notifier()

    def pubimmediate(self, topic: str, *args: Any, **kwargs: Any) -> None:
        callable_tuples = self._get_callable_tuples(topic)
        for _obj, fn in callable_tuples:
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                LOGGER.exception("PubSub immediate handler failed for %s: %s", topic, exc)

    def sub(self, obj: Any, method_name: str, topic: str) -> None:
        with self._lock:
            objects = self._topics_to_objects.setdefault(topic, weakref.WeakSet())
            objects.add(obj)
            self._topics_to_method_names[topic].add(method_name)

    def wait_on_pub(self, timeout: float = 0.5) -> None:
        self._received_job_event.wait(timeout)
        self._received_job_event.clear()

    def wake(self) -> None:
        self._received_job_event.set()

    def work_to_do(self) -> bool:
        with self._lock:
            return bool(self._pubs)

    def clear(self) -> None:
        with self._lock:
            self._topics_to_objects.clear()
            self._topics_to_method_names.clear()
            self._pubs.clear()

    def set_notifier(self, notifier: Callable[[], None] | None) -> None:
        self._notifier = notifier

    def set_valid_callable(self, valid_callable: Callable[[Any], bool]) -> None:
        self._valid_callable = valid_callable
