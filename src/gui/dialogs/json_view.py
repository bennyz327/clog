"""Read-only JSON viewer dialog (used by Posts maintenance tab)."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)


class JsonViewDialog(QDialog):
    def __init__(
        self,
        title: str,
        payload: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(720, 520)

        layout = QVBoxLayout(self)

        view = QPlainTextEdit(self)
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        text = self._format_payload(payload)
        view.setPlainText(text)
        layout.addWidget(view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.clicked.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _format_payload(payload: Any) -> str:
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
                return json.dumps(parsed, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                return payload
        try:
            return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            return repr(payload)
