from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit, QPlainTextEdit

from core.constants import UserError
from ._base import WriteDialog


class AddWorkDialog(WriteDialog):
    title = "新增工作紀錄"

    def build_form(self, form: QFormLayout) -> None:
        self._content = QPlainTextEdit()
        self._content.setPlaceholderText("work log content (markdown or plain text)")
        self._content.setMinimumHeight(160)

        if self.locked_target is None:
            self._target = QLineEdit()
            self._target.setPlaceholderText("#id, URL, or name")
            form.addRow("Target", self._target)
        else:
            self._target = None

        form.addRow("Content", self._content)
        self._content.setFocus()

    def invoke(self) -> dict[str, Any]:
        target = self.locked_target_clause()
        if target is None:
            assert self._target is not None
            target = self._target.text().strip()
        content = self._content.toPlainText().strip()
        if not target:
            raise UserError("target is required")
        if not content:
            raise UserError("content is required")
        return self.controller.write("add_work", target, content)
