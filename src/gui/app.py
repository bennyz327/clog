from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSplashScreen

from core.config import LOGGER, default_db_path, setup_logging, shutdown_logging
from core.controller import Controller
from .main_window import ClogMainWindow
from .pubsub_bridge import QtPubSubBridge
from .resources import app_icon, splash_pixmap
from .themes import ThemeName, apply_theme


def main() -> int:
    setup_logging()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("clog")
    app.setOrganizationName("clog")
    app.setWindowIcon(app_icon())

    splash: QSplashScreen | None = None
    pm = splash_pixmap()
    if pm is not None:
        splash = QSplashScreen(pm, Qt.WindowType.WindowStaysOnTopHint)
        splash.show()
        app.processEvents()

    db_path = default_db_path()
    controller = Controller(db_path)

    theme_pref = controller.get_setting("user", "theme", "light")
    theme: ThemeName = "dark" if theme_pref == "dark" else "light"
    apply_theme(app, theme)

    bridge = QtPubSubBridge(controller.pubsub, app)
    window = ClogMainWindow(controller, bridge)
    window.show()
    if splash is not None:
        splash.finish(window)

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
