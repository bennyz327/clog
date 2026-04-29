from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit, QSpinBox

from core.constants import UserError
from ._base import WriteDialog


class AddPostDialog(WriteDialog):
    title = "Record Post"

    def __init__(self, controller, parent=None, *, default_target: str | None = None):
        self._default_target = default_target or ""
        super().__init__(controller, parent)

    def build_form(self, form: QFormLayout) -> None:
        self._url = QLineEdit()
        self._url.setPlaceholderText("post URL")
        self._target = QLineEdit(self._default_target)
        self._target.setPlaceholderText("optional: #id, URL, or name (verify-only)")
        self._note = QLineEdit()
        self._note.setPlaceholderText("optional note")
        self._timeout = QSpinBox()
        self._timeout.setRange(5, 600)
        self._timeout.setValue(60)
        self._timeout.setSuffix(" s")
        form.addRow("URL", self._url)
        form.addRow("Target", self._target)
        form.addRow("Note", self._note)
        form.addRow("Timeout", self._timeout)
        self._url.setFocus()

    def invoke(self) -> dict[str, Any]:
        url = self._url.text().strip()
        if not url:
            raise UserError("URL is required")
        target = self._target.text().strip() or None
        note = self._note.text().strip() or None
        return self.controller.write(
            "add_post", url, target=target, note=note, timeout=int(self._timeout.value())
        )
