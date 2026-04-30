from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QPlainTextEdit

from core.constants import UserError
from ._base import WriteDialog


class EditWorkDialog(WriteDialog):
    title = "編輯工作紀錄"

    def __init__(
        self,
        controller,
        parent=None,
        *,
        work_id: int,
        content: str,
        locked_target: dict[str, Any] | None = None,
    ):
        self._work_id = int(work_id)
        self._initial_content = content or ""
        super().__init__(controller, parent, locked_target=locked_target)

    def build_form(self, form: QFormLayout) -> None:
        self._content = QPlainTextEdit()
        self._content.setPlainText(self._initial_content)
        self._content.setMinimumHeight(220)
        form.addRow("內容", self._content)
        self._content.setFocus()

    def invoke(self) -> dict[str, Any]:
        content = self._content.toPlainText().strip()
        if not content:
            raise UserError("content is required")
        return self.controller.write("update_work", self._work_id, content=content)
