"""Right-bottom docked notification panel (spec §11).

Behavior modeled after hydrus ClientGUIPopupMessages:

- Pinned to the bottom-right of the parent (main) window, NOT a free-floating
  toast. Re-positions when the parent moves/resizes.
- Default state: shows ONLY the latest message + a summary bar (`📩 N`).
- User clicks the expand button to see the full stack inside a scroll area.
- Messages are sticky by default — they do not auto-disappear. Each card has
  a Dismiss button. The summary bar has a Dismiss All button.
- Levels: info / warning / error / success (success kept for back-compat).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


@dataclass
class NotificationPayload:
    title: str
    text: str
    level: str = "info"
    detail: str | None = None
    timeout_ms: int = 0
    sticky: bool = True  # spec §11.4: messages do not auto-disappear by default
    timestamp: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_any(cls, payload: "NotificationPayload | dict[str, Any] | str") -> "NotificationPayload":
        if isinstance(payload, NotificationPayload):
            return payload
        if isinstance(payload, str):
            return cls(title="Notice", text=payload)
        # dict path
        return cls(
            title=str(payload.get("title") or "Notice"),
            text=str(payload.get("text") or ""),
            level=str(payload.get("level") or "info"),
            detail=str(payload["detail"]) if payload.get("detail") else None,
            timeout_ms=int(payload.get("timeout_ms") or 0),
            sticky=bool(payload.get("sticky", True)),
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
        root.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel(payload.title, self)
        title.setObjectName("NotificationTitle")
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: bold;")
        ts = QLabel(payload.timestamp.strftime("%H:%M:%S"), self)
        ts.setObjectName("NotificationTimestamp")
        ts.setStyleSheet("color: gray;")
        header.addWidget(title, 1)
        header.addWidget(ts, 0)
        root.addLayout(header)

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


class NotificationSummaryBar(QFrame):
    """Compact bar shown above the latest card; provides expand + dismiss-all."""

    def __init__(self, manager: "NotificationManager", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self.setObjectName("NotificationSummaryBar")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 6, 4)
        layout.setSpacing(8)

        self._count_label = QLabel("0 則訊息", self)
        layout.addWidget(self._count_label, 1)

        self._expand_button = QPushButton("展開", self)
        self._expand_button.setFlat(True)
        self._expand_button.clicked.connect(self._manager.toggle_expanded)
        layout.addWidget(self._expand_button)

        self._dismiss_all = QPushButton("Dismiss All", self)
        self._dismiss_all.setFlat(True)
        self._dismiss_all.clicked.connect(self._manager.dismiss_all)
        layout.addWidget(self._dismiss_all)

    def update_state(self, count: int, expanded: bool) -> None:
        self._count_label.setText(f"📩 {count} 則訊息")
        self._expand_button.setText("收合" if expanded else "展開")
        self._expand_button.setEnabled(count > 0)
        self._dismiss_all.setEnabled(count > 0)


class NotificationManager(QFrame):
    """Bottom-right docked notification panel for the main window."""

    MAX_CARDS = 50  # hard cap to bound memory; far above any realistic count
    EXPAND_RATIO = 0.6  # max expanded height = 60% of parent window

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("NotificationManager")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        self._cards: deque[NotificationCard] = deque()
        self._expanded = False
        self._anchor_widget: QWidget | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._summary = NotificationSummaryBar(self, self)
        outer.addWidget(self._summary)

        # Latest-card holder (shown in collapsed mode).
        self._latest_holder = QFrame(self)
        self._latest_holder.setFrameShape(QFrame.Shape.NoFrame)
        self._latest_layout = QVBoxLayout(self._latest_holder)
        self._latest_layout.setContentsMargins(0, 0, 0, 0)
        self._latest_layout.setSpacing(0)
        outer.addWidget(self._latest_holder)

        # Full stack (shown when expanded).
        self._stack_scroll = QScrollArea(self)
        self._stack_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._stack_scroll.setWidgetResizable(True)
        self._stack_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._stack_holder = QWidget()
        self._stack_layout = QVBoxLayout(self._stack_holder)
        self._stack_layout.setContentsMargins(0, 0, 0, 0)
        self._stack_layout.setSpacing(6)
        self._stack_scroll.setWidget(self._stack_holder)
        self._stack_scroll.setVisible(False)
        outer.addWidget(self._stack_scroll)

        # Sensible minimum so the docked panel never collapses to 0×0 even
        # when there is just the summary bar (which would visually look like
        # the notification "disappeared").
        self.setMinimumWidth(280)
        self.setMinimumHeight(self._summary.sizeHint().height())

        self.hide()
        parent.installEventFilter(self)
        self.bind_anchor_widget()
        self._update_summary()

    # ── event filter for parent move/resize ────────────────────────────────
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        parent = self.parentWidget()
        if event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Move,
            QEvent.Type.Show,
            QEvent.Type.WindowStateChange,
        ):
            if watched is parent or (
                isinstance(parent, QMainWindow)
                and parent.centralWidget() is not None
                and watched is parent.centralWidget()
            ):
                self.reposition()
        return super().eventFilter(watched, event)

    # ── public API ─────────────────────────────────────────────────────────
    def add_message(self, payload: NotificationPayload | dict[str, Any] | str) -> None:
        self.bind_anchor_widget()
        normalized = NotificationPayload.from_any(payload)
        card = NotificationCard(normalized, self.dismiss, self._stack_holder)
        self._cards.append(card)
        # Both layouts; the same card object will only ever live in one.
        self._stack_layout.insertWidget(0, card)
        # Cap memory.
        while len(self._cards) > self.MAX_CARDS:
            oldest = self._cards.popleft()
            self._stack_layout.removeWidget(oldest)
            oldest.deleteLater()
        self._refresh_views()
        self.show()
        self.raise_()
        self.reposition()

    def dismiss(self, card: NotificationCard) -> None:
        try:
            self._cards.remove(card)
        except ValueError:
            pass
        # Remove from whichever layout it currently lives in.
        self._stack_layout.removeWidget(card)
        self._latest_layout.removeWidget(card)
        card.setParent(None)
        card.deleteLater()
        self._refresh_views()
        if not self._cards:
            self.hide()
        else:
            self.reposition()

    def dismiss_all(self) -> None:
        while self._cards:
            self.dismiss(self._cards[-1])

    def toggle_expanded(self) -> None:
        if not self._cards:
            return
        self._expanded = not self._expanded
        self._refresh_views()
        self.reposition()

    def bind_anchor_widget(self) -> None:
        """Track the current central widget so dock positioning follows it."""
        parent = self.parentWidget()
        anchor: QWidget | None = None
        if isinstance(parent, QMainWindow):
            anchor = parent.centralWidget()
        if anchor is self._anchor_widget:
            return
        if self._anchor_widget is not None:
            self._anchor_widget.removeEventFilter(self)
        self._anchor_widget = anchor
        if self._anchor_widget is not None:
            self._anchor_widget.installEventFilter(self)
        if not self.isHidden():
            self.reposition()

    # ── view sync ──────────────────────────────────────────────────────────
    def _refresh_views(self) -> None:
        self._update_summary()
        if self._expanded:
            # Move every card into the stack layout, hide the collapsed slot.
            while self._latest_layout.count() > 0:
                item = self._latest_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(self._stack_holder)
                    self._stack_layout.insertWidget(0, w)
            for card in self._cards:
                if self._stack_layout.indexOf(card) < 0:
                    card.setParent(self._stack_holder)
                    self._stack_layout.insertWidget(0, card)
            self._stack_scroll.setVisible(True)
            self._latest_holder.setVisible(False)
        else:
            # Collapsed: hide stack; mount only the newest card in latest slot.
            self._stack_scroll.setVisible(False)
            # Detach any card from latest_layout first.
            while self._latest_layout.count() > 0:
                item = self._latest_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(self._stack_holder)
                    self._stack_layout.insertWidget(0, w)
            if self._cards:
                latest = self._cards[-1]
                self._stack_layout.removeWidget(latest)
                latest.setParent(self._latest_holder)
                self._latest_layout.addWidget(latest)
            self._latest_holder.setVisible(True)

    def _update_summary(self) -> None:
        self._summary.update_state(len(self._cards), self._expanded)

    # ── placement ──────────────────────────────────────────────────────────
    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None or self.isHidden():
            return
        self.bind_anchor_widget()

        # Anchor against the centralWidget rect (in parent coords) so we sit
        # above the statusBar and below the menuBar, regardless of the
        # main-window chrome height.
        if self._anchor_widget is not None:
            anchor = self._anchor_widget.geometry()
            anchor_right = anchor.right() + 1
            anchor_bottom = anchor.bottom() + 1
            anchor_height = anchor.height()
        else:
            anchor_right = parent.width()
            anchor_bottom = parent.height()
            anchor_height = parent.height()

        if self._expanded:
            max_h = max(120, int(anchor_height * self.EXPAND_RATIO))
            self.setMaximumHeight(max_h)
            self.setMinimumWidth(max(220, min(360, anchor_right - 40)))
        else:
            self.setMaximumHeight(16777215)  # Qt default
            self.setMinimumWidth(max(220, min(320, anchor_right - 40)))
        self.adjustSize()

        margin = 16
        x = anchor_right - self.width() - margin
        y = anchor_bottom - self.height() - margin
        # On very narrow windows, snap to right edge with no horizontal margin.
        if x < margin:
            x = max(0, anchor_right - self.width())
        if y < margin:
            y = max(0, anchor_bottom - self.height())
        self.move(QPoint(x, y))
        # Ensure the panel is always above sibling widgets (centralWidget /
        # docks). raise_() is cheap and necessary because some layout passes
        # silently re-stack siblings.
        self.raise_()
