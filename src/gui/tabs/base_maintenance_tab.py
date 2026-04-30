"""Common base for the per-creator maintenance tabs.

Layout convention:

  ┌─ toolbar (新增 / 重新整理 / ...) ─────────────────────┐
  │                                                       │
  ├─ body (subclass-specific) ─────────────────────────── │
  │                                                       │
  └───────────────────────────────────────────────────────┘

Subclasses override:

- ``_build_body(layout: QVBoxLayout)`` — populate the body area
- ``_run_refresh()`` — fetch + render
- ``_open_add_dialog()`` (optional) — invoked by the 新增 button
- right-click handlers as needed

The base also wires the standard `creator_id` property and a
``locked_target`` dict that maintenance tabs forward to dialogs.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.config import LOGGER
from core.controller import Controller


class BaseMaintenanceTab(QWidget):
    """Shared scaffolding for URLs / Aliases / Posts / Reminder / Worklogs tabs."""

    add_label: str = "新增"
    show_add_button: bool = True

    def __init__(
        self,
        controller: Controller,
        creator_id: int,
        *,
        creator_display_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._creator_id = int(creator_id)
        self._creator_display_name = creator_display_name or ""

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self._toolbar_layout = QHBoxLayout()
        self._toolbar_layout.setSpacing(6)
        if self.show_add_button:
            self._add_button = QPushButton(self.add_label)
            self._add_button.clicked.connect(self._open_add_dialog)
            self._toolbar_layout.addWidget(self._add_button)
        self._refresh_button = QPushButton("重新整理")
        self._refresh_button.clicked.connect(self.refresh)
        self._toolbar_layout.addWidget(self._refresh_button)
        self._toolbar_layout.addStretch(1)
        root.addLayout(self._toolbar_layout)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        self._build_body(body)
        root.addLayout(body, 1)

        self.refresh()

    # ── public API ─────────────────────────────────────────────────────────
    @property
    def creator_id(self) -> int:
        return self._creator_id

    @property
    def locked_target(self) -> dict[str, Any]:
        return {
            "creator_id": self._creator_id,
            "display_name": self._creator_display_name,
        }

    def refresh(self) -> None:
        try:
            self._run_refresh()
        except Exception as exc:
            LOGGER.exception("%s refresh failed: %s", type(self).__name__, exc)

    # ── overridable hooks ──────────────────────────────────────────────────
    def _build_body(self, layout: QVBoxLayout) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _run_refresh(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _open_add_dialog(self) -> None:
        # Default no-op; subclasses override.
        pass

    # ── helpers ────────────────────────────────────────────────────────────
    def add_toolbar_button(self, text: str, slot, *, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(slot)
        if primary:
            button.setDefault(True)
        # Insert before the trailing stretch.
        self._toolbar_layout.insertWidget(self._toolbar_layout.count() - 1, button)
        return button

    def set_add_button_enabled(self, enabled: bool) -> None:
        if hasattr(self, "_add_button"):
            self._add_button.setEnabled(enabled)
            self._add_button.setVisible(enabled or not self.show_add_button)
