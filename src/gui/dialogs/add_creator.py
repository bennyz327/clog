from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit

from core.constants import UserError
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
            raise UserError("name is required")
        url = self._url.text().strip() or None
        note = self._note.text().strip() or None
        if url:
            result = self.controller.write(
                "add_creator",
                name,
                url=url,
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
        return self.controller.write("add_creator", name, url=None, note=note)

    def success_text(self, result: dict[str, Any]) -> str:
        if result.get("queued"):
            return "已寫入並排入後台工作"
        return super().success_text(result)
