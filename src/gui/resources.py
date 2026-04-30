"""Static resource access (icons / splash) for the GUI.

Resolves ``static/<name>`` regardless of whether the app is running from
source or from a PyInstaller onedir bundle (where bundled data lives at
``<contents_directory>/static/`` and Qt sets ``sys._MEIPASS`` accordingly).

See ``static/spec.txt`` for the canonical resource roster.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap

# Force-load Qt6Svg.dll so the SVG icon engine plugin (qsvgicon.dll) registers.
# Without this, QIcon(svg_path).pixmap(...) silently returns a null pixmap.
from PySide6 import QtSvg  # noqa: F401


_THIS_DIR = Path(__file__).resolve().parent  # src/gui
_REPO_STATIC = _THIS_DIR.parent.parent / "static"  # <repo>/static


def resource_path(name: str) -> Path:
    """Return the on-disk path for ``static/<name>`` (may not exist)."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = Path(bundle) / "static" / name
        if candidate.is_file():
            return candidate
    return _REPO_STATIC / name


def app_icon() -> QIcon:
    """Multi-size app icon (taskbar / window title / file explorer).

    Falls back to ``app.png`` when ``app.ico`` is missing (eg. dev checkout
    without the .ico generated yet).
    """
    ico = resource_path("app.ico")
    if ico.is_file():
        return QIcon(str(ico))
    png = resource_path("app.png")
    if png.is_file():
        return QIcon(str(png))
    return QIcon()


def theme_icon(theme: str) -> QIcon:
    """Inline icon variant matching the current theme.

    Per ``static/spec.txt``: ``app-dark.svg`` is the dark/main-color icon
    used on light backgrounds; ``app-light.svg`` is white for dark
    backgrounds. So:

    - light theme  → app-dark.svg
    - dark theme   → app-light.svg
    """
    name = "app-light.svg" if theme == "dark" else "app-dark.svg"
    p = resource_path(name)
    if p.is_file():
        return QIcon(str(p))
    return app_icon()


def splash_pixmap() -> QPixmap | None:
    p = resource_path("app-splash.png")
    if not p.is_file():
        return None
    pm = QPixmap(str(p))
    return pm if not pm.isNull() else None
