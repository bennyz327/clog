from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import Any, Callable

from .config import LOGGER

Subscriber = Callable[..., Any]


class PubSub:
    """Thread-safe topic bus.

    Subscribers are called synchronously on whichever thread published the event.
    GUI code should wrap its handlers (see clog.gui.pubsub_bridge) so the actual
    work runs on the Qt main thread.
    """

    def __init__(self) -> None:
        self._topics: dict[str, list[Subscriber]] = defaultdict(list)
        self._lock = RLock()

    def sub(self, topic: str, fn: Subscriber) -> None:
        with self._lock:
            handlers = self._topics[topic]
            if fn not in handlers:
                handlers.append(fn)

    def unsub(self, topic: str, fn: Subscriber) -> None:
        with self._lock:
            handlers = self._topics.get(topic)
            if not handlers:
                return
            try:
                handlers.remove(fn)
            except ValueError:
                pass
            if not handlers:
                self._topics.pop(topic, None)

    def pub(self, topic: str, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            handlers = list(self._topics.get(topic, ()))
        for fn in handlers:
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                LOGGER.exception("PubSub handler failed for %s: %s", topic, exc)

    def clear(self) -> None:
        with self._lock:
            self._topics.clear()
