from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.constants import AmbiguousTarget, UserError
from core.config import LOGGER
from core.controller import Controller
from ..notifications import NotificationPayload


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

    def success_text(self, result: dict[str, Any]) -> str:
        return f"{self.title} completed."

    def _notify(
        self,
        title: str,
        text: str,
        *,
        level: str = "info",
        detail: str | None = None,
        sticky: bool = False,
        timeout_ms: int = 4500,
    ) -> None:
        self._controller.pubsub.pub(
            "message",
            payload=NotificationPayload(
                title=title,
                text=text,
                level=level,
                detail=detail,
                sticky=sticky,
                timeout_ms=timeout_ms,
            ),
        )

    def _on_submit(self) -> None:
        try:
            result = self.invoke()
        except (UserError, AmbiguousTarget) as exc:
            self._notify(self.title, str(exc) or exc.__class__.__name__, level="warning", sticky=True)
            return
        except Exception as exc:
            LOGGER.exception("Dialog %s failed: %s", self.title, exc)
            self._notify(
                self.title,
                f"Unexpected failure: {exc}",
                level="error",
                detail=repr(exc),
                sticky=True,
            )
            return
        if isinstance(result, dict) and result.get("warning"):
            self._notify(self.title, str(result["warning"]), level="warning", sticky=True)
        self._notify(self.title, self.success_text(result if isinstance(result, dict) else {}), level="success")
        self.accept_result(result if isinstance(result, dict) else {})
