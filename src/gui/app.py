from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from core.config import LOGGER, default_db_path, setup_logging, shutdown_logging
from core.controller import Controller
from .main_window import ClogMainWindow
from .pubsub_bridge import QtPubSubBridge
from .themes import ThemeName, apply_theme


def main() -> int:
    setup_logging()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("clog")
    app.setOrganizationName("clog")

    db_path = default_db_path()
    controller = Controller(db_path)

    theme_pref = controller.get_setting("user", "theme", "light")
    theme: ThemeName = "dark" if theme_pref == "dark" else "light"
    apply_theme(app, theme)

    bridge = QtPubSubBridge(controller.pubsub, app)
    window = ClogMainWindow(controller, bridge)
    window.show()

    try:
        LOGGER.info("GUI loop start db=%s theme=%s", db_path, theme)
        rc = int(app.exec())
        LOGGER.info("GUI loop end rc=%s", rc)
        return rc
    finally:
        controller.shutdown()
        shutdown_logging()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
