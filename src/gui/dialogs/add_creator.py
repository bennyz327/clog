from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit

from ._base import WriteDialog


class AddCreatorDialog(WriteDialog):
    title = "新增創作者"

    def build_form(self, form: QFormLayout) -> None:
        self._name = QLineEdit()
        self._name.setPlaceholderText("creator name")
        self._url = QLineEdit()
        self._url.setPlaceholderText("https://platform/profile (optional)")
        self._note = QLineEdit()
        self._note.setPlaceholderText("optional note")
        form.addRow("Name", self._name)
        form.addRow("URL", self._url)
        form.addRow("Note", self._note)
        self._name.setFocus()

    def invoke(self) -> dict[str, Any]:
        name = self._name.text().strip()
        if not name:
            from core.constants import UserError
            raise UserError("name is required")
        url = self._url.text().strip() or None
        note = self._note.text().strip() or None
        return self.controller.write("add_creator", name, url=url, note=note)
