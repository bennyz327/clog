"""Left-pane search widget.

Replaces the old creators-list + search tabs. Single search input on top,
result table below with two columns: 名稱 / 搜尋匹配.

Signals
-------
- ``creatorSelected(int)`` — single click; carries creator_id, GUI should
  remember selection but NOT touch the right pane (per spec §7.3).
- ``creatorActivated(int)`` — double click; carries creator_id, GUI should
  open / switch the Overview tab (per spec §7.3).
- ``contextRequested(int, QPoint, str)`` — right click; creator_id +
  global_pos + display_name. The main window opens the 5-item context menu
  (per spec §7.4) using these args.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config import LOGGER
from core.controller import Controller


# kind labels follow the search_fts kinds (see db/__init__.py).
_KIND_ZH: dict[str, str] = {
    "creator": "創作者",
    "profile": "平台",
    "profile_url": "網址",
    "alias": "別名",
    "post": "貼文",
    "work": "工作紀錄",
    "reminder": "提醒",
}


def _format_match(snippet: str) -> str:
    """Map a ``kind:title`` snippet to ``<型別>-<摘要>`` per spec §7.2."""
    if ":" not in snippet:
        return snippet
    kind, title = snippet.split(":", 1)
    label = _KIND_ZH.get(kind, kind)
    return f"{label}-{title}" if title else label


class SearchPane(QWidget):
    creatorSelected = Signal(int)
    creatorActivated = Signal(int, str)
    contextRequested = Signal(int, QPoint, str)

    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._groups: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self._input = QLineEdit()
        self._input.setPlaceholderText("搜尋創作者 / 別名 / profile / 貼文 ...（Enter）")
        self._input.returnPressed.connect(self.run_search)
        layout.addWidget(self._input)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["名稱", "搜尋匹配"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(self._on_double_click)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table, 1)

    # ── public API ─────────────────────────────────────────────────────────
    def focus_input(self) -> None:
        self._input.setFocus()
        self._input.selectAll()

    def current_query(self) -> str:
        return self._input.text().strip()

    def run_search(self, *, silent: bool = False) -> None:
        query = self.current_query()
        if not query:
            self._table.setRowCount(0)
            self._groups = []
            return
        try:
            groups: list[dict[str, Any]] = self._controller.read("grouped_search", query)
        except Exception as exc:
            LOGGER.exception("search failed: %s", exc)
            if not silent:
                # surface error via pubsub so notification pane shows it
                from ..notifications import NotificationPayload
                self._controller.pubsub.pub(
                    "message",
                    payload=NotificationPayload(
                        title="Search Failed",
                        text=str(exc),
                        level="error",
                        detail=repr(exc),
                        sticky=True,
                    ),
                )
            return
        self._groups = groups
        self._render_rows(groups)

    def refresh(self) -> None:
        if self.current_query():
            self.run_search(silent=True)

    def selected_group(self) -> dict[str, Any] | None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._groups):
            return None
        return self._groups[row]

    # ── render ─────────────────────────────────────────────────────────────
    def _render_rows(self, groups: list[dict[str, Any]]) -> None:
        table = self._table
        table.setRowCount(0)
        for index, group in enumerate(groups):
            creator = group["creator"]
            snippets = group.get("snippets") or []
            kind_counts = group.get("kind_counts") or {}
            match_lines = [_format_match(s) for s in snippets]
            if not match_lines:
                # Fall back to kind counts when snippets are empty (e.g. only
                # the creator row matched — exact name hit).
                for kind, count in kind_counts.items():
                    label = _KIND_ZH.get(kind, kind)
                    match_lines.append(label if count == 1 else f"{label}×{count}")
            display = creator["primary_name"]
            status = ""
            try:
                status = creator["status"] or ""
            except (IndexError, KeyError):
                pass
            table.insertRow(index)
            name_item = QTableWidgetItem(display)
            name_item.setData(Qt.ItemDataRole.UserRole, int(creator["id"]))
            name_item.setToolTip(f"#{creator['id']} · {status}")
            match_item = QTableWidgetItem("\n".join(match_lines))
            match_item.setToolTip("\n".join(match_lines))
            table.setItem(index, 0, name_item)
            table.setItem(index, 1, match_item)
        table.resizeRowsToContents()

    # ── event handlers ─────────────────────────────────────────────────────
    def _row_creator_id(self, row: int) -> int | None:
        if row < 0:
            return None
        item = self._table.item(row, 0)
        if item is None:
            return None
        cid = item.data(Qt.ItemDataRole.UserRole)
        return int(cid) if isinstance(cid, int) else None

    def _row_display_name(self, row: int) -> str:
        if row < 0:
            return ""
        item = self._table.item(row, 0)
        return item.text() if item else ""

    def _on_selection_changed(self) -> None:
        cid = self._row_creator_id(self._table.currentRow())
        if cid is not None:
            self.creatorSelected.emit(cid)

    def _on_double_click(self, item: QTableWidgetItem) -> None:
        row = item.row()
        cid = self._row_creator_id(row)
        if cid is None:
            return
        self.creatorActivated.emit(cid, self._row_display_name(row))

    def _on_context_menu(self, pos: QPoint) -> None:
        row = self._table.indexAt(pos).row()
        cid = self._row_creator_id(row)
        if cid is None:
            return
        global_pos = self._table.viewport().mapToGlobal(pos)
        self.contextRequested.emit(cid, global_pos, self._row_display_name(row))
