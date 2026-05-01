from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit, QSpinBox

from core.constants import UserError
from ._base import WriteDialog


class AddPostDialog(WriteDialog):
    title = "新增貼文"

    def build_form(self, form: QFormLayout) -> None:
        self._url = QLineEdit()
        self._url.setPlaceholderText("post URL")
        self._note = QLineEdit()
        self._note.setPlaceholderText("optional note")
        self._timeout = QSpinBox()
        self._timeout.setRange(5, 600)
        self._timeout.setValue(60)
        self._timeout.setSuffix(" s")

        form.addRow("URL", self._url)

        if self.locked_target is None:
            self._target = QLineEdit()
            self._target.setPlaceholderText("optional: #id, URL, or name (verify-only)")
            form.addRow("Target", self._target)
        else:
            self._target = None

        form.addRow("Note", self._note)
        form.addRow("Timeout", self._timeout)
        self._url.setFocus()

    def invoke(self) -> dict[str, Any]:
        url = self._url.text().strip()
        if not url:
            raise UserError("URL is required")
        target = self.locked_target_clause()
        if target is None and self._target is not None:
            target = self._target.text().strip() or None
        note = self._note.text().strip() or None
        timeout = int(self._timeout.value())
        receipt = self.controller.submit_background_job(
            "command.add_post",
            {"url": url, "target": target, "note": note, "timeout": timeout},
            dedupe_key=f"command.add_post:{url}",
        )
        return {"queued": True, "job_id": receipt["job_id"]}

    def success_text(self, result: dict[str, Any]) -> str:
        return "已排入後台工作"
