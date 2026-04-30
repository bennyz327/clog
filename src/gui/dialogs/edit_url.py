from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit

from core.constants import UserError
from ._base import WriteDialog


class EditUrlDialog(WriteDialog):
    title = "編輯網址"

    def __init__(
        self,
        controller,
        parent=None,
        *,
        url_id: int,
        url: str,
        note: str | None,
        locked_target: dict[str, Any] | None = None,
    ):
        self._url_id = int(url_id)
        self._initial_url = url or ""
        self._initial_note = note or ""
        super().__init__(controller, parent, locked_target=locked_target)

    def build_form(self, form: QFormLayout) -> None:
        self._url = QLineEdit(self._initial_url)
        self._note = QLineEdit(self._initial_note)
        form.addRow("URL", self._url)
        form.addRow("備註", self._note)
        self._url.setFocus()
        self._url.selectAll()

    def invoke(self) -> dict[str, Any]:
        url = self._url.text().strip()
        if not url:
            raise UserError("URL is required")
        note = self._note.text().strip() or None
        return self.controller.write("update_url", self._url_id, url=url, note=note)
