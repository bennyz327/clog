"""Worklogs maintenance tab — list + preview + edit/delete (spec §10.6)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.config import LOGGER
from ..dialogs import AddWorkDialog, EditWorkDialog
from .base_maintenance_tab import BaseMaintenanceTab


class WorklogsTab(BaseMaintenanceTab):
    HEADERS = ["#", "摘要", "建立時間", "更新時間"]

    def __init__(self, *args, **kwargs) -> None:
        self._orientation = Qt.Orientation.Horizontal
        self._render_markdown = False
        super().__init__(*args, **kwargs)

    def _build_body(self, layout: QVBoxLayout) -> None:
        self.add_toolbar_button("水平/垂直", self._toggle_orientation)
        self._md_button = self.add_toolbar_button("Markdown 渲染", self._toggle_md)

        self._splitter = QSplitter(self._orientation)

        self._table = QTableWidget(0, len(self.HEADERS))
        self._table.setHorizontalHeaderLabels(self.HEADERS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.itemDoubleClicked.connect(self._on_double_click)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._splitter.addWidget(self._table)

        self._preview_plain = QPlainTextEdit()
        self._preview_plain.setReadOnly(True)
        self._preview_plain.setPlaceholderText("（雙擊或選中一筆紀錄以預覽）")
        self._preview_md = QTextBrowser()
        self._preview_md.setOpenExternalLinks(True)
        self._preview_md.setVisible(False)
        preview_holder = QWidget()
        preview_layout = QVBoxLayout(preview_holder)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self._preview_plain, 1)
        preview_layout.addWidget(self._preview_md, 1)
        self._splitter.addWidget(preview_holder)

        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 4)
        layout.addWidget(self._splitter, 1)

    # ── public navigation ──────────────────────────────────────────────────
    def focus(self, *, focus_id: int | None = None, **_kwargs: Any) -> None:
        if focus_id is None:
            return
        target = int(focus_id)
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 0)
            if item is None:
                continue
            row = item.data(Qt.ItemDataRole.UserRole) or {}
            if int(row.get("id", -1)) == target:
                self._table.selectRow(r)
                self._table.scrollToItem(item)
                self._render_preview(row)
                return

    # ── refresh ────────────────────────────────────────────────────────────
    def _run_refresh(self) -> None:
        rows = self._controller.read("creator_worklogs", self._creator_id)
        table = self._table
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            work_id = int(row["id"])
            content = row["content"] or ""
            summary = (content.splitlines()[0] if content else "")[:120]
            cells = [
                f"#{work_id}",
                summary,
                str(row["created_at"] or "")[:19],
                str(row["updated_at"] or "")[:19],
            ]
            for col, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, dict(row))
                item.setToolTip(value)
                table.setItem(r, col, item)
        table.resizeColumnsToContents()
        if rows:
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    # ── selection / preview ────────────────────────────────────────────────
    def _selected_row(self) -> dict[str, Any] | None:
        r = self._table.currentRow()
        if r < 0:
            return None
        item = self._table.item(r, 0)
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _on_double_click(self, item: QTableWidgetItem) -> None:
        row = self._table.item(item.row(), 0)
        if row is None:
            return
        data = row.data(Qt.ItemDataRole.UserRole)
        if data:
            self._render_preview(data)

    def _on_selection_changed(self) -> None:
        row = self._selected_row()
        if row:
            self._render_preview(row)

    def _render_preview(self, row: dict[str, Any]) -> None:
        content = row.get("content") or ""
        if self._render_markdown:
            self._preview_md.setMarkdown(content)
        else:
            self._preview_plain.setPlainText(content)

    def _toggle_orientation(self) -> None:
        self._orientation = (
            Qt.Orientation.Vertical
            if self._orientation == Qt.Orientation.Horizontal
            else Qt.Orientation.Horizontal
        )
        self._splitter.setOrientation(self._orientation)

    def _toggle_md(self) -> None:
        self._render_markdown = not self._render_markdown
        self._preview_plain.setVisible(not self._render_markdown)
        self._preview_md.setVisible(self._render_markdown)
        self._md_button.setText("純文字渲染" if self._render_markdown else "Markdown 渲染")
        row = self._selected_row()
        if row:
            self._render_preview(row)

    # ── add / edit / delete ────────────────────────────────────────────────
    def _open_add_dialog(self) -> None:
        AddWorkDialog(self._controller, self, locked_target=self.locked_target).exec()

    def _on_context_menu(self, pos: QPoint) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        self._table.selectRow(index.row())
        row = self._selected_row()
        if row is None:
            return
        menu = QMenu(self)
        menu.addAction("編輯", lambda: self._edit(row))
        menu.addAction("刪除", lambda: self._delete(row))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _edit(self, row: dict[str, Any]) -> None:
        EditWorkDialog(
            self._controller,
            self,
            work_id=int(row["id"]),
            content=row["content"] or "",
            locked_target=self.locked_target,
        ).exec()

    def _delete(self, row: dict[str, Any]) -> None:
        confirm = QMessageBox.question(
            self,
            "刪除工作紀錄",
            f"確定要刪除 #{row['id']}？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self._controller.write("delete_work", int(row["id"]))
        except Exception as exc:
            LOGGER.exception("delete_work failed: %s", exc)
            QMessageBox.warning(self, "刪除失敗", str(exc))
