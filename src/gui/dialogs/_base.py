from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.constants import AmbiguousTarget, UserError
from core.config import LOGGER
from core.controller import Controller


class WriteDialog(QDialog):
    """Base for any dialog that calls ``controller.write(...)``.

    Subclasses build their form via ``build_form(layout)`` and produce the
    write call via ``invoke()``. On success ``accept_result(result)`` runs
    and the dialog closes; on UserError / AmbiguousTarget a QMessageBox
    shows the error and the dialog stays open.
    """

    title: str = "clog"

    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.setWindowTitle(self.title)
        self.setModal(True)
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        self._form = QFormLayout()
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        outer.addLayout(self._form)

        self.build_form(self._form)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = self._buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if isinstance(ok_button, QPushButton):
            ok_button.setObjectName("HydrusAccept")
            ok_button.setText("Submit")
        if isinstance(cancel_button, QPushButton):
            cancel_button.setObjectName("HydrusCancel")
        self._buttons.accepted.connect(self._on_submit)
        self._buttons.rejected.connect(self.reject)
        outer.addWidget(self._buttons)

    @property
    def controller(self) -> Controller:
        return self._controller

    def build_form(self, form: QFormLayout) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def invoke(self) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def accept_result(self, result: dict[str, Any]) -> None:
        """Hook for subclasses; default just accepts."""
        self.accept()

    def _on_submit(self) -> None:
        try:
            result = self.invoke()
        except (UserError, AmbiguousTarget) as exc:
            QMessageBox.warning(self, self.title, str(exc) or exc.__class__.__name__)
            return
        except Exception as exc:
            LOGGER.exception("Dialog %s failed: %s", self.title, exc)
            QMessageBox.critical(self, self.title, f"Unexpected failure: {exc}")
            return
        if isinstance(result, dict) and result.get("warning"):
            QMessageBox.information(self, self.title, str(result["warning"]))
        self.accept_result(result if isinstance(result, dict) else {})
