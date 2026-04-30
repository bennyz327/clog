"""Per-creator multi-tab notebook for the right pane.

System manages tab lifecycle (open/close/switch). Users do not create empty
tabs. Same (creator_id, kind) is never duplicated — opening it again just
switches focus and refreshes (per spec §8.2 / §8.3).

Tab kinds: ``Overview``, ``URLs``, ``Aliases``, ``Posts``, ``Reminder``,
``Worklogs``. Callers register factories via :py:meth:`register_factory`.
Tab widgets must expose:

- ``__init__(controller, creator_id, **kwargs)``
- ``creator_id`` attribute or property
- ``refresh()`` callable
- optionally ``focus(**kwargs)`` for cross-tab navigation

Tab UX (Phase C, modeled after hydrus tab notebook):

- middle-click closes a tab
- right-click on a tab opens: 關閉本 tab / 關閉其他 / 關閉右側 / 重新整理本 tab
- drag tabs to reorder (built-in via ``setMovable(True)``)
- long titles get elided + horizontal scroll buttons
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QLabel, QMenu, QTabBar, QTabWidget, QWidget

from core.config import LOGGER
from core.controller import Controller


TabFactory = Callable[[Controller, int], QWidget]


class ClogTabBar(QTabBar):
    """Tab bar with middle-click-to-close support."""

    middleClicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setExpanding(False)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            idx = self.tabAt(event.pos())
            if idx >= 0:
                self.middleClicked.emit(idx)
                event.accept()
                return
        super().mouseReleaseEvent(event)


class RightNotebook(QTabWidget):
    """Owns the right-side per-creator tabs."""

    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._factories: dict[str, TabFactory] = {}
        self._index: dict[tuple[int, str], QWidget] = {}
        self._creator_names: dict[int, str] = {}

        self._tab_bar = ClogTabBar(self)
        self.setTabBar(self._tab_bar)
        self._tab_bar.middleClicked.connect(self._close_index)
        self._tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)

        self.setMovable(True)
        self.setTabsClosable(True)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabCloseRequested.connect(self._close_index)

        self._placeholder = QLabel(
            "（雙擊左欄搜尋結果可開啟 Overview tab）",
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        self._placeholder.setMargin(40)
        self.addTab(self._placeholder, "請先選擇創作者")
        self.tabBar().setTabButton(0, self.tabBar().ButtonPosition.RightSide, None)

    # ── factory registration ───────────────────────────────────────────────
    def register_factory(self, kind: str, factory: TabFactory) -> None:
        self._factories[kind] = factory

    # ── identity helpers ───────────────────────────────────────────────────
    def remember_creator_name(self, creator_id: int, primary_name: str) -> None:
        self._creator_names[creator_id] = primary_name

    def _build_title(self, kind: str, creator_id: int) -> str:
        return f"{kind} - {self.get_display_name(creator_id)} (#{creator_id})"

    def get_display_name(self, creator_id: int) -> str:
        """Return the creator's primary_name, hitting the cache first."""
        cached = self._creator_names.get(creator_id)
        if cached:
            return cached
        try:
            snapshot = self._controller.read("creator_snapshot", creator_id)
            name = str(snapshot["creator"]["primary_name"])
            self._creator_names[creator_id] = name
            return name
        except Exception:
            return f"creator-{creator_id}"

    # ── core API ───────────────────────────────────────────────────────────
    def switch_or_open(
        self,
        kind: str,
        creator_id: int,
        *,
        refresh: bool = True,
        **focus_kwargs: Any,
    ) -> QWidget | None:
        factory = self._factories.get(kind)
        if factory is None:
            LOGGER.warning("RightNotebook: no factory for kind=%s", kind)
            return None

        key = (creator_id, kind)
        widget = self._index.get(key)
        if widget is None:
            widget = factory(self._controller, creator_id)
            self._index[key] = widget
            title = self._build_title(kind, creator_id)
            self._dismiss_placeholder()
            new_index = self.addTab(widget, title)
            self.setTabToolTip(new_index, title)
            self.setCurrentIndex(new_index)
        else:
            self.setCurrentWidget(widget)
            if refresh and hasattr(widget, "refresh"):
                try:
                    widget.refresh()
                except Exception as exc:
                    LOGGER.exception("refresh tab %s failed: %s", kind, exc)

        if focus_kwargs and hasattr(widget, "focus"):
            try:
                widget.focus(**focus_kwargs)
            except Exception as exc:
                LOGGER.exception("focus tab %s failed: %s", kind, exc)
        return widget

    def refresh_all_tabs(self) -> None:
        for widget in list(self._index.values()):
            if hasattr(widget, "refresh"):
                try:
                    widget.refresh()
                except Exception as exc:
                    LOGGER.exception("refresh failed for %s: %s", widget, exc)

    def refresh_creator_tabs(self, creator_id: int) -> None:
        for (cid, _kind), widget in self._index.items():
            if cid == creator_id and hasattr(widget, "refresh"):
                try:
                    widget.refresh()
                except Exception as exc:
                    LOGGER.exception("refresh failed for %s: %s", widget, exc)

    def close_creator_tabs(self, creator_id: int) -> None:
        for key in [k for k in self._index if k[0] == creator_id]:
            widget = self._index.pop(key)
            idx = self.indexOf(widget)
            if idx >= 0:
                self.removeTab(idx)
                widget.deleteLater()
        self._maybe_show_placeholder()

    # ── close handlers ─────────────────────────────────────────────────────
    def _close_index(self, index: int) -> None:
        widget = self.widget(index)
        if widget is None or widget is self._placeholder:
            return
        for key, w in list(self._index.items()):
            if w is widget:
                self._index.pop(key)
                break
        self.removeTab(index)
        widget.deleteLater()
        self._maybe_show_placeholder()

    def _close_others(self, keep_index: int) -> None:
        kept_widget = self.widget(keep_index)
        if kept_widget is None or kept_widget is self._placeholder:
            return
        for i in reversed(range(self.count())):
            if i == self.indexOf(kept_widget):
                continue
            widget = self.widget(i)
            if widget is self._placeholder:
                continue
            self._close_index(i)

    def _close_right(self, anchor_index: int) -> None:
        anchor_widget = self.widget(anchor_index)
        if anchor_widget is None or anchor_widget is self._placeholder:
            return
        # Close from the rightmost end down to anchor+1 to keep indices stable.
        for i in reversed(range(self.indexOf(anchor_widget) + 1, self.count())):
            widget = self.widget(i)
            if widget is self._placeholder:
                continue
            self._close_index(i)

    def _refresh_tab_at(self, index: int) -> None:
        widget = self.widget(index)
        if widget is None or widget is self._placeholder:
            return
        if hasattr(widget, "refresh"):
            try:
                widget.refresh()
            except Exception as exc:
                LOGGER.exception("refresh tab failed: %s", exc)

    # ── context menu ───────────────────────────────────────────────────────
    def _on_tab_context_menu(self, pos: QPoint) -> None:
        index = self._tab_bar.tabAt(pos)
        if index < 0:
            return
        widget = self.widget(index)
        if widget is self._placeholder:
            return
        menu = QMenu(self)
        menu.addAction("關閉本 tab", lambda i=index: self._close_index(i))
        menu.addAction("關閉其他 tab", lambda i=index: self._close_others(i))
        menu.addAction("關閉右側 tab", lambda i=index: self._close_right(i))
        menu.addSeparator()
        menu.addAction("重新整理本 tab", lambda i=index: self._refresh_tab_at(i))
        menu.exec(self._tab_bar.mapToGlobal(pos))

    # ── placeholder lifecycle ──────────────────────────────────────────────
    def _dismiss_placeholder(self) -> None:
        idx = self.indexOf(self._placeholder)
        if idx >= 0:
            self.removeTab(idx)

    def _maybe_show_placeholder(self) -> None:
        if self.count() == 0:
            self.addTab(self._placeholder, "請先選擇創作者")
            self.tabBar().setTabButton(0, self.tabBar().ButtonPosition.RightSide, None)
