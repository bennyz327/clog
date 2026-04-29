from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit, QPlainTextEdit

from core.constants import UserError
from ._base import WriteDialog


class AddWorkDialog(WriteDialog):
    title = "Add Work Log"

    def __init__(self, controller, parent=None, *, default_target: str | None = None):
        self._default_target = default_target or ""
        super().__init__(controller, parent)

    def build_form(self, form: QFormLayout) -> None:
        self._target = QLineEdit(self._default_target)
        self._target.setPlaceholderText("#id, URL, or name")
        self._content = QPlainTextEdit()
        self._content.setPlaceholderText("work log content (markdown or plain text)")
        self._content.setMinimumHeight(160)
        form.addRow("Target", self._target)
        form.addRow("Content", self._content)
        (self._content if self._default_target else self._target).setFocus()

    def invoke(self) -> dict[str, Any]:
        target = self._target.text().strip()
        content = self._content.toPlainText().strip()
        if not target:
            raise UserError("target is required")
        if not content:
            raise UserError("content is required")
        return self.controller.write("add_work", target, content)
