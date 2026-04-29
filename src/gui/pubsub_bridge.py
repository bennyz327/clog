from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

from core.config import LOGGER
from core.pubsub import PubSub


class QtPubSubBridge(QObject):
    """Bridges core PubSub onto the Qt main thread.

    Subscribers registered through this bridge always run on the thread that
    owns the bridge (typically the main thread), regardless of which thread
    called ``pubsub.pub(...)``.
    """

    _trampoline = Signal(object, tuple, dict)

    def __init__(self, pubsub: PubSub, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pubsub = pubsub
        self._wrappers: list[tuple[str, Callable[..., Any]]] = []
        self._trampoline.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    def sub(self, topic: str, fn: Callable[..., Any]) -> None:
        def wrapper(*args: Any, **kwargs: Any) -> None:
            self._trampoline.emit(fn, args, kwargs)

        self._pubsub.sub(topic, wrapper)
        self._wrappers.append((topic, wrapper))

    @Slot(object, tuple, dict)
    def _dispatch(self, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as exc:
            LOGGER.exception("PubSub bridge dispatch failed: %s", exc)

    def shutdown(self) -> None:
        for topic, wrapper in self._wrappers:
            self._pubsub.unsub(topic, wrapper)
        self._wrappers.clear()
