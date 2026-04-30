from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit

from core.constants import UserError
from ._base import WriteDialog


class EditAliasDialog(WriteDialog):
    title = "編輯別名"

    def __init__(
        self,
        controller,
        parent=None,
        *,
        alias_id: int,
        name: str,
        reason: str | None,
        note: str | None,
        locked_target: dict[str, Any] | None = None,
    ):
        self._alias_id = int(alias_id)
        self._initial_name = name or ""
        self._initial_reason = reason or ""
        self._initial_note = note or ""
        super().__init__(controller, parent, locked_target=locked_target)

    def build_form(self, form: QFormLayout) -> None:
        self._name = QLineEdit(self._initial_name)
        self._reason = QLineEdit(self._initial_reason)
        self._reason.setPlaceholderText("e.g. generic-alias / profile-rename")
        self._note = QLineEdit(self._initial_note)
        form.addRow("名稱", self._name)
        form.addRow("來源", self._reason)
        form.addRow("備註", self._note)
        self._name.setFocus()
        self._name.selectAll()

    def invoke(self) -> dict[str, Any]:
        name = self._name.text().strip()
        if not name:
            raise UserError("name is required")
        reason = self._reason.text().strip() or None
        note = self._note.text().strip() or None
        return self.controller.write(
            "update_alias", self._alias_id, name=name, reason=reason, note=note
        )
