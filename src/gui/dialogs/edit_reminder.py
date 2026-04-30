from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit, QSpinBox

from core.constants import UserError
from ._base import WriteDialog


class EditReminderDialog(WriteDialog):
    title = "編輯提醒"

    def __init__(
        self,
        controller,
        parent=None,
        *,
        creator_id: int,
        next_due_at: str,
        interval_days: int | None,
        note: str | None,
        locked_target: dict[str, Any] | None = None,
    ):
        self._creator_id = int(creator_id)
        self._initial_due = next_due_at or ""
        self._initial_interval = int(interval_days or 0)
        self._initial_note = note or ""
        super().__init__(controller, parent, locked_target=locked_target)

    def build_form(self, form: QFormLayout) -> None:
        self._when = QLineEdit(self._initial_due)
        self._when.setPlaceholderText("YYYY-MM-DD, today, tomorrow, or Nd")
        self._interval = QSpinBox()
        self._interval.setRange(0, 365)
        self._interval.setSpecialValueText("(none)")
        self._interval.setSuffix(" days")
        self._interval.setValue(self._initial_interval)
        self._note = QLineEdit(self._initial_note)
        form.addRow("下次提醒", self._when)
        form.addRow("間隔", self._interval)
        form.addRow("備註", self._note)
        self._when.setFocus()
        self._when.selectAll()

    def invoke(self) -> dict[str, Any]:
        when = self._when.text().strip()
        if not when:
            raise UserError("when is required")
        interval = int(self._interval.value()) or None
        note = self._note.text().strip() or None
        return self.controller.write(
            "update_reminder", self._creator_id, when, interval_days=interval, note=note
        )
