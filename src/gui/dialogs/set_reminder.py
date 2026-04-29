from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit, QSpinBox

from core.constants import UserError
from ._base import WriteDialog


class SetReminderDialog(WriteDialog):
    title = "Set Reminder"

    def __init__(self, controller, parent=None, *, default_target: str | None = None):
        self._default_target = default_target or ""
        super().__init__(controller, parent)

    def build_form(self, form: QFormLayout) -> None:
        self._target = QLineEdit(self._default_target)
        self._target.setPlaceholderText("#id, URL, or name")
        self._when = QLineEdit()
        self._when.setPlaceholderText("YYYY-MM-DD, today, tomorrow, or Nd")
        self._interval = QSpinBox()
        self._interval.setRange(0, 365)
        self._interval.setSpecialValueText("(none)")
        self._interval.setSuffix(" days")
        self._note = QLineEdit()
        self._note.setPlaceholderText("optional note")
        form.addRow("Target", self._target)
        form.addRow("When", self._when)
        form.addRow("Interval", self._interval)
        form.addRow("Note", self._note)
        (self._when if self._default_target else self._target).setFocus()

    def invoke(self) -> dict[str, Any]:
        target = self._target.text().strip()
        when = self._when.text().strip()
        if not target:
            raise UserError("target is required")
        if not when:
            raise UserError("when is required")
        interval = int(self._interval.value()) or None
        note = self._note.text().strip() or None
        return self.controller.write(
            "set_reminder", target, when, interval_days=interval, note=note
        )
