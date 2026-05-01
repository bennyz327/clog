from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLineEdit

from core.constants import UserError
from core.utils import is_url
from ._base import WriteDialog


class AddNameDialog(WriteDialog):
    title = "新增別名"

    def build_form(self, form: QFormLayout) -> None:
        self._name = QLineEdit()
        self._name.setPlaceholderText("alias or new display name")
        self._context = QLineEdit()
        self._context.setPlaceholderText("optional: platform name OR creator-page URL")

        if self.locked_target is None:
            self._target = QLineEdit()
            self._target.setPlaceholderText("#id, URL, or name")
            form.addRow("Target", self._target)
        else:
            self._target = None

        form.addRow("Name", self._name)
        form.addRow("Context", self._context)
        self._name.setFocus()

    def invoke(self) -> dict[str, Any]:
        target = self.locked_target_clause()
        if target is None:
            assert self._target is not None
            target = self._target.text().strip()
        name = self._name.text().strip()
        if not target:
            raise UserError("target is required")
        if not name:
            raise UserError("name is required")
        context = self._context.text().strip() or None
        if context and is_url(context):
            result = self.controller.write(
                "add_name",
                target,
                name,
                context=context,
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
        return self.controller.write("add_name", target, name, context=context)

    def success_text(self, result: dict[str, Any]) -> str:
        if result.get("queued"):
            return "已寫入並排入後台工作"
        return super().success_text(result)
