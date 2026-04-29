from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit

from core.constants import UserError
from ._base import WriteDialog


class AddNameDialog(WriteDialog):
    title = "Add Name"

    def __init__(self, controller, parent=None, *, default_target: str | None = None):
        self._default_target = default_target or ""
        super().__init__(controller, parent)

    def build_form(self, form: QFormLayout) -> None:
        self._target = QLineEdit(self._default_target)
        self._target.setPlaceholderText("#id, URL, or name")
        self._name = QLineEdit()
        self._name.setPlaceholderText("alias or new display name")
        self._context = QLineEdit()
        self._context.setPlaceholderText("optional: platform name OR creator-page URL")
        form.addRow("Target", self._target)
        form.addRow("Name", self._name)
        form.addRow("Context", self._context)
        (self._name if self._default_target else self._target).setFocus()

    def invoke(self) -> dict[str, Any]:
        target = self._target.text().strip()
        name = self._name.text().strip()
        if not target:
            raise UserError("target is required")
        if not name:
            raise UserError("name is required")
        context = self._context.text().strip() or None
        return self.controller.write("add_name", target, name, context=context)
