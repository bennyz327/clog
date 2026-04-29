from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from shiboken6 import isValid

from core.config import LOGGER
from core.pubsub import PubSub

PUBSUB_EVENT_TYPE = QEvent.registerEventType()


class PubSubEvent(QEvent):
    def __init__(self) -> None:
        super().__init__(QEvent.Type(PUBSUB_EVENT_TYPE))


class QtPubSubBridge(QObject):
    """Installs a Hydrus-style pubsub event catcher on the Qt app object."""

    def __init__(self, pubsub: PubSub, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pubsub = pubsub
        self.installEventFilter(self)
        self._pubsub.set_notifier(self.publish_queued)
        self._pubsub.set_valid_callable(isValid)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        try:
            if event.type() == QEvent.Type(PUBSUB_EVENT_TYPE) and isinstance(event, PubSubEvent):
                if self._pubsub.work_to_do():
                    self._pubsub.process()
                event.accept()
                return True
        except Exception as exc:
            LOGGER.exception("QtPubSubBridge eventFilter failed: %s", exc)
            return True
        return False

    def publish_queued(self) -> None:
        if self.parent() is not None and isValid(self.parent()):
            QCoreApplication.postEvent(self, PubSubEvent())

    def shutdown(self) -> None:
        self._pubsub.set_notifier(None)
        self.removeEventFilter(self)
