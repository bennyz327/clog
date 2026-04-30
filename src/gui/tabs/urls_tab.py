"""URLs maintenance tab — list + edit/delete profile_urls for one creator."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.config import LOGGER
from ..dialogs import AddUrlDialog, EditUrlDialog, JsonViewDialog
from .base_maintenance_tab import BaseMaintenanceTab


class UrlsTab(BaseMaintenanceTab):
    add_label = "新增"
    HEADERS = ["#", "platform", "URL", "備註", "更新時間"]

    def _build_body(self, layout: QVBoxLayout) -> None:
        self._table = QTableWidget(0, len(self.HEADERS))
        self._table.setHorizontalHeaderLabels(self.HEADERS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table, 1)

    def _run_refresh(self) -> None:
        rows = self._controller.read("creator_urls", self._creator_id)
        table = self._table
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            url_id = int(row["url_id"])
            platform = (row["platform"] or "—") + (f":{row['platform_id']}" if row["platform_id"] else "")
            cells = [
                f"#{url_id}",
                platform,
                row["url"] or "",
                row["note"] or "",
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
            header = table.horizontalHeader()
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

    def _selected_row(self) -> dict[str, Any] | None:
        r = self._table.currentRow()
        if r < 0:
            return None
        item = self._table.item(r, 0)
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    # ── interactions ───────────────────────────────────────────────────────
    def _open_add_dialog(self) -> None:
        AddUrlDialog(self._controller, self, locked_target=self.locked_target).exec()

    def _on_context_menu(self, pos: QPoint) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        self._table.selectRow(index.row())
        row = self._selected_row()
        if row is None:
            return
        menu = QMenu(self)
        menu.addAction("關聯的 profile 摘要", lambda: self._show_profile_summary(row))
        menu.addAction("編輯", lambda: self._edit(row))
        menu.addAction("刪除", lambda: self._delete(row))
        menu.addSeparator()
        menu.addAction("複製網址", lambda: self._copy_url(row))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _edit(self, row: dict[str, Any]) -> None:
        EditUrlDialog(
            self._controller,
            self,
            url_id=int(row["url_id"]),
            url=row["url"],
            note=row["note"],
            locked_target=self.locked_target,
        ).exec()

    def _delete(self, row: dict[str, Any]) -> None:
        confirm = QMessageBox.question(
            self,
            "刪除網址",
            f"確定要刪除這個網址？\n\n{row['url']}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self._controller.write("delete_url", int(row["url_id"]))
        except Exception as exc:
            LOGGER.exception("delete_url failed: %s", exc)
            QMessageBox.warning(self, "刪除失敗", str(exc))

    def _copy_url(self, row: dict[str, Any]) -> None:
        QGuiApplication.clipboard().setText(row["url"] or "")

    def _show_profile_summary(self, row: dict[str, Any]) -> None:
        payload = {
            "profile_id": row["profile_id"],
            "platform": row["platform"],
            "platform_id": row["platform_id"],
            "display_name": row["display_name"],
            "identity_state": row["identity_state"],
            "url": row["url"],
            "canonical_url": row["canonical_url"],
            "from_url": row["from_url"],
            "reason": row["reason"],
            "status": row["status"],
            "note": row["note"],
        }
        JsonViewDialog("關聯 profile 摘要", payload, self).exec()
