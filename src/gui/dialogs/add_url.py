from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit

from core.constants import UserError
from ._base import WriteDialog


class AddUrlDialog(WriteDialog):
    title = "Add URL"

    def __init__(self, controller, parent=None, *, default_target: str | None = None):
        self._default_target = default_target or ""
        super().__init__(controller, parent)

    def build_form(self, form: QFormLayout) -> None:
        self._target = QLineEdit(self._default_target)
        self._target.setPlaceholderText("#id, URL, or name")
        self._url = QLineEdit()
        self._url.setPlaceholderText("https://platform/profile")
        self._note = QLineEdit()
        self._note.setPlaceholderText("optional note")
        form.addRow("Target", self._target)
        form.addRow("URL", self._url)
        form.addRow("Note", self._note)
        (self._url if self._default_target else self._target).setFocus()

    def invoke(self) -> dict[str, Any]:
        target = self._target.text().strip()
        url = self._url.text().strip()
        if not target:
            raise UserError("target is required")
        if not url:
            raise UserError("URL is required")
        note = self._note.text().strip() or None
        return self.controller.write("add_url", target, url, note=note)
