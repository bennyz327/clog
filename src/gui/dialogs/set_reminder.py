from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit, QSpinBox

from core.constants import UserError
from ._base import WriteDialog


class SetReminderDialog(WriteDialog):
    title = "設定提醒"

    def build_form(self, form: QFormLayout) -> None:
        self._when = QLineEdit()
        self._when.setPlaceholderText("YYYY-MM-DD, today, tomorrow, or Nd")
        self._interval = QSpinBox()
        self._interval.setRange(0, 365)
        self._interval.setSpecialValueText("(none)")
        self._interval.setSuffix(" days")
        self._note = QLineEdit()
        self._note.setPlaceholderText("optional note")

        if self.locked_target is None:
            self._target = QLineEdit()
            self._target.setPlaceholderText("#id, URL, or name")
            form.addRow("Target", self._target)
        else:
            self._target = None

        form.addRow("When", self._when)
        form.addRow("Interval", self._interval)
        form.addRow("Note", self._note)
        self._when.setFocus()

    def invoke(self) -> dict[str, Any]:
        target = self.locked_target_clause()
        if target is None:
            assert self._target is not None
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
