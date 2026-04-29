from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from core.config import LOGGER, default_db_path
from core.controller import Controller
from .main_window import ClogMainWindow
from .pubsub_bridge import QtPubSubBridge
from .themes import ThemeName, apply_theme


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("clog")
    app.setOrganizationName("clog")

    db_path = default_db_path()
    controller = Controller(db_path)

    theme_pref = controller.options.get("theme")
    theme: ThemeName = "dark" if theme_pref == "dark" else "light"
    apply_theme(app, theme)

    bridge = QtPubSubBridge(controller.pubsub)
    window = ClogMainWindow(controller, bridge)
    window.show()

    LOGGER.info("GUI loop start db=%s theme=%s", db_path, theme)
    rc = int(app.exec())
    LOGGER.info("GUI loop end rc=%s", rc)
    controller.shutdown()
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
