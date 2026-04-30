"""Reminder maintenance tab — single-row detail card per spec §10.5."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QLabel,
    QMenu,
    QMessageBox,
    QVBoxLayout,
)

from core.config import LOGGER
from ..dialogs import EditReminderDialog, SetReminderDialog
from .base_maintenance_tab import BaseMaintenanceTab


class ReminderTab(BaseMaintenanceTab):
    add_label = "新增"

    def __init__(self, *args, **kwargs) -> None:
        self._reminder: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)

    def _build_body(self, layout: QVBoxLayout) -> None:
        self._card = QFrame()
        self._card.setFrameShape(QFrame.Shape.StyledPanel)
        self._card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._card.customContextMenuRequested.connect(self._on_context_menu)

        self._form = QFormLayout(self._card)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._form.setContentsMargins(16, 14, 16, 14)
        self._form.setVerticalSpacing(8)

        self._due_label = QLabel("—")
        self._due_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        self._interval_label = QLabel("—")
        self._note_label = QLabel("")
        self._note_label.setWordWrap(True)
        self._created_label = QLabel("—")
        self._updated_label = QLabel("—")

        self._form.addRow("下次提醒", self._due_label)
        self._form.addRow("間隔", self._interval_label)
        self._form.addRow("備註", self._note_label)
        self._form.addRow("建立時間", self._created_label)
        self._form.addRow("更新時間", self._updated_label)

        self._placeholder = QLabel(
            "目前沒有提醒。\n點擊上方「新增」按鈕為這位創作者建立提醒。",
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        self._placeholder.setStyleSheet("color: gray; padding: 32px;")

        layout.addWidget(self._card)
        layout.addWidget(self._placeholder)
        layout.addStretch(1)

    def _run_refresh(self) -> None:
        reminder = self._controller.read("creator_reminder", self._creator_id)
        if reminder is None:
            self._reminder = None
            self._card.setVisible(False)
            self._placeholder.setVisible(True)
            self.set_add_button_enabled(True)
        else:
            self._reminder = dict(reminder)
            self._due_label.setText(str(reminder["next_due_at"] or "—"))
            self._interval_label.setText(
                f"每 {reminder['interval_days']} 天" if reminder["interval_days"] else "(無)"
            )
            self._note_label.setText(str(reminder["note"] or ""))
            self._created_label.setText(str(reminder["created_at"] or "")[:19])
            self._updated_label.setText(str(reminder["updated_at"] or "")[:19])
            self._card.setVisible(True)
            self._placeholder.setVisible(False)
            self.set_add_button_enabled(False)

    # ── interactions ───────────────────────────────────────────────────────
    def _open_add_dialog(self) -> None:
        if self._reminder is not None:
            return
        SetReminderDialog(
            self._controller, self, locked_target=self.locked_target
        ).exec()

    def _on_context_menu(self, pos: QPoint) -> None:
        if self._reminder is None:
            return
        menu = QMenu(self)
        menu.addAction("編輯", self._edit)
        menu.addAction("刪除", self._delete)
        menu.exec(self._card.mapToGlobal(pos))

    def _edit(self) -> None:
        if self._reminder is None:
            return
        EditReminderDialog(
            self._controller,
            self,
            creator_id=self._creator_id,
            next_due_at=str(self._reminder["next_due_at"] or ""),
            interval_days=self._reminder["interval_days"],
            note=self._reminder["note"],
            locked_target=self.locked_target,
        ).exec()

    def _delete(self) -> None:
        if self._reminder is None:
            return
        confirm = QMessageBox.question(
            self,
            "刪除提醒",
            "確定要刪除這個提醒？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self._controller.write("delete_reminder", self._creator_id)
        except Exception as exc:
            LOGGER.exception("delete_reminder failed: %s", exc)
            QMessageBox.warning(self, "刪除失敗", str(exc))
