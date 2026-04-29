from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ThemeName = Literal["light", "dark"]


def available_themes() -> list[ThemeName]:
    return ["light", "dark"]


def apply_theme(app: QApplication, name: ThemeName) -> None:
    if name == "dark":
        _apply_dark_palette(app)
    else:
        app.setStyle("Fusion")
        app.setPalette(app.style().standardPalette())
    app.setStyleSheet(_load_qss(name))


def _apply_dark_palette(app: QApplication) -> None:
    """Hydrus-style dark palette: cool slate base with mid-cyan accent."""
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#2b2b2b"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#dcdcdc"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#232629"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2e3236"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#dcdcdc"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#3c3f41"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#dcdcdc"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#3c3f41"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#dcdcdc"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#3a78b8"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#5fb3ff"))
    palette.setColor(QPalette.ColorRole.LinkVisited, QColor("#a9a3ff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#888888"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ff7171"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#777777"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#777777"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#777777"))
    app.setPalette(palette)


def _load_qss(name: ThemeName) -> str:
    fname = "dark.qss" if name == "dark" else "light.qss"
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", ".")) / "gui" / "themes"
    else:
        base = Path(__file__).resolve().parent
    return (base / fname).read_text(encoding="utf-8")
