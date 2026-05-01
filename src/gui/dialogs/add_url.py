from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit

from core.constants import UserError
from ._base import WriteDialog


class AddUrlDialog(WriteDialog):
    title = "新增網址"

    def build_form(self, form: QFormLayout) -> None:
        self._url = QLineEdit()
        self._url.setPlaceholderText("https://platform/profile")
        self._note = QLineEdit()
        self._note.setPlaceholderText("optional note")

        if self.locked_target is None:
            self._target = QLineEdit()
            self._target.setPlaceholderText("#id, URL, or name")
            form.addRow("Target", self._target)
        else:
            self._target = None

        form.addRow("URL", self._url)
        form.addRow("Note", self._note)
        self._url.setFocus()

    def invoke(self) -> dict[str, Any]:
        target = self.locked_target_clause()
        if target is None:
            assert self._target is not None
            target = self._target.text().strip()
        url = self._url.text().strip()
        if not target:
            raise UserError("target is required")
        if not url:
            raise UserError("URL is required")
        note = self._note.text().strip() or None
        result = self.controller.write(
            "add_url",
            target,
            url,
            note=note,
            resolve_metadata=False,
        )
        if result.get("needs_worker") and result.get("url_id") is not None:
            receipt = self.controller.submit_background_job(
                "profile.resolve_metadata",
                {"profile_url_id": int(result["url_id"])},
                dedupe_key=f"profile.resolve_metadata:{int(result['url_id'])}",
            )
            result["queued"] = True
            result["job_id"] = receipt["job_id"]
        return result

    def success_text(self, result: dict[str, Any]) -> str:
        if result.get("queued"):
            return "已寫入並排入後台工作"
        return super().success_text(result)
