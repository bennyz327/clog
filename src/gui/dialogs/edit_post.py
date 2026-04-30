from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QLabel, QLineEdit

from core.constants import UserError
from ._base import WriteDialog


class EditPostDialog(WriteDialog):
    title = "編輯貼文"

    def __init__(
        self,
        controller,
        parent=None,
        *,
        post_id: int,
        url: str,
        note: str | None,
        title_text: str | None,
        platform_post_id: str | None,
        locked_target: dict[str, Any] | None = None,
    ):
        self._post_id = int(post_id)
        self._initial_url = url or ""
        self._initial_note = note or ""
        self._initial_title = title_text or ""
        self._platform_post_id = platform_post_id or ""
        super().__init__(controller, parent, locked_target=locked_target)

    def build_form(self, form: QFormLayout) -> None:
        # platform_post_id is read-only — derived from gdl, never user-editable
        self._post_id_label = QLabel(self._platform_post_id or "(未設定)")
        self._post_id_label.setEnabled(False)
        self._url = QLineEdit(self._initial_url)
        self._title_edit = QLineEdit(self._initial_title)
        self._note = QLineEdit(self._initial_note)
        form.addRow("Post ID（不可改）", self._post_id_label)
        form.addRow("URL", self._url)
        form.addRow("標題", self._title_edit)
        form.addRow("備註", self._note)
        self._url.setFocus()
        self._url.selectAll()

    def invoke(self) -> dict[str, Any]:
        url = self._url.text().strip()
        if not url:
            raise UserError("URL is required")
        note = self._note.text().strip() or None
        title = self._title_edit.text().strip() or None
        return self.controller.write(
            "update_post", self._post_id, url=url, note=note, title=title
        )
