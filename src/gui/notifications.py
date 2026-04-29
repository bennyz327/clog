from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


@dataclass
class NotificationPayload:
    title: str
    text: str
    level: str = "info"
    detail: str | None = None
    timeout_ms: int = 4500
    sticky: bool = False

    @classmethod
    def from_any(cls, payload: "NotificationPayload | dict[str, Any] | str") -> "NotificationPayload":
        if isinstance(payload, NotificationPayload):
            return payload
        if isinstance(payload, str):
            return cls(title="Notice", text=payload)
        return cls(
            title=str(payload.get("title") or "Notice"),
            text=str(payload.get("text") or ""),
            level=str(payload.get("level") or "info"),
            detail=str(payload["detail"]) if payload.get("detail") else None,
            timeout_ms=int(payload.get("timeout_ms") or 4500),
            sticky=bool(payload.get("sticky", False)),
        )


class NotificationCard(QFrame):
    def __init__(self, payload: NotificationPayload, dismiss_cb, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.payload = payload
        self._dismiss_cb = dismiss_cb
        self.setObjectName(f"NotificationCard-{payload.level}")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        title = QLabel(payload.title, self)
        title.setObjectName("NotificationTitle")
        title.setWordWrap(True)
        root.addWidget(title)

        body = QLabel(payload.text, self)
        body.setObjectName("NotificationText")
        body.setWordWrap(True)
        root.addWidget(body)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        if payload.detail:
            copy_btn = QPushButton("Copy Detail", self)
            copy_btn.clicked.connect(self._copy_detail)
            button_row.addWidget(copy_btn)
        dismiss_btn = QPushButton("Dismiss", self)
        dismiss_btn.clicked.connect(lambda: self._dismiss_cb(self))
        button_row.addWidget(dismiss_btn)
        root.addLayout(button_row)

        self._timer: QTimer | None = None
        if not payload.sticky and payload.timeout_ms > 0:
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(lambda: self._dismiss_cb(self))
            self._timer.start(payload.timeout_ms)

    def _copy_detail(self) -> None:
        if self.payload.detail:
            QGuiApplication.clipboard().setText(self.payload.detail)


class NotificationManager(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("NotificationManager")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        self._cards: deque[NotificationCard] = deque()
        self._max_cards = 5

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._cards_holder = QWidget(self)
        self._cards_layout = QVBoxLayout(self._cards_holder)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        layout.addWidget(self._cards_holder)

        controls = QHBoxLayout()
        controls.addStretch(1)
        dismiss_all = QPushButton("Dismiss All", self)
        dismiss_all.clicked.connect(self.dismiss_all)
        controls.addWidget(dismiss_all)
        layout.addLayout(controls)

        self.hide()
        parent.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Move,
            QEvent.Type.WindowStateChange,
        ):
            self.reposition()
        return super().eventFilter(watched, event)

    def add_message(self, payload: NotificationPayload | dict[str, Any] | str) -> None:
        card = NotificationCard(NotificationPayload.from_any(payload), self.dismiss, self._cards_holder)
        self._cards.append(card)
        self._cards_layout.insertWidget(0, card)
        while len(self._cards) > self._max_cards:
            oldest = self._cards.popleft()
            self._cards_layout.removeWidget(oldest)
            oldest.hide()
            oldest.deleteLater()
        self.show()
        self.raise_()
        self.reposition()

    def dismiss(self, card: NotificationCard) -> None:
        try:
            self._cards.remove(card)
        except ValueError:
            pass
        self._cards_layout.removeWidget(card)
        card.hide()
        card.deleteLater()
        if not self._cards:
            self.hide()
        else:
            self.reposition()

    def dismiss_all(self) -> None:
        while self._cards:
            self.dismiss(self._cards[-1])

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None or self.isHidden():
            return
        self.adjustSize()
        x = parent.width() - self.width() - 20
        y = parent.height() - self.height() - 20
        self.move(QPoint(max(0, x), max(0, y)))
