"""Aliases maintenance tab — list + edit/delete creator_aliases."""

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
from ..dialogs import AddNameDialog, EditAliasDialog, JsonViewDialog
from .base_maintenance_tab import BaseMaintenanceTab


class AliasesTab(BaseMaintenanceTab):
    HEADERS = ["#", "別名", "來源", "綁定 profile", "狀態"]

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
        header.setStretchLastSection(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table, 1)

    def _run_refresh(self) -> None:
        rows = self._controller.read("creator_aliases", self._creator_id)
        table = self._table
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            alias_id = int(row["id"])
            profile_label = "—"
            if row["profile_id"]:
                bits = []
                if row["platform"]:
                    bits.append(str(row["platform"]))
                if row["platform_id"]:
                    bits.append(f":{row['platform_id']}")
                if row["display_name"]:
                    bits.append(f" {row['display_name']}")
                profile_label = "".join(bits) or f"profile #{row['profile_id']}"
            cells = [
                f"#{alias_id}",
                row["name"] or "",
                row["reason"] or "",
                profile_label,
                row["status"] or "",
            ]
            for col, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, dict(row))
                item.setToolTip(value)
                table.setItem(r, col, item)
        table.resizeColumnsToContents()

    def _selected_row(self) -> dict[str, Any] | None:
        r = self._table.currentRow()
        if r < 0:
            return None
        item = self._table.item(r, 0)
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    # ── interactions ───────────────────────────────────────────────────────
    def _open_add_dialog(self) -> None:
        AddNameDialog(self._controller, self, locked_target=self.locked_target).exec()

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
        menu.addSeparator()
        menu.addAction("複製名稱", lambda: self._copy_name(row))
        menu.addAction("顯示綁定層級資訊", lambda: self._show_binding(row))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _edit(self, row: dict[str, Any]) -> None:
        EditAliasDialog(
            self._controller,
            self,
            alias_id=int(row["id"]),
            name=row["name"],
            reason=row["reason"],
            note=row["note"],
            locked_target=self.locked_target,
        ).exec()

    def _delete(self, row: dict[str, Any]) -> None:
        confirm = QMessageBox.question(
            self,
            "刪除別名",
            f"確定要刪除別名「{row['name']}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self._controller.write("delete_alias", int(row["id"]))
        except Exception as exc:
            LOGGER.exception("delete_alias failed: %s", exc)
            QMessageBox.warning(self, "刪除失敗", str(exc))

    def _copy_name(self, row: dict[str, Any]) -> None:
        QGuiApplication.clipboard().setText(row["name"] or "")

    def _show_binding(self, row: dict[str, Any]) -> None:
        payload = {
            "alias_id": row["id"],
            "creator_id": row["creator_id"],
            "profile_id": row["profile_id"],
            "platform": row["platform"],
            "platform_id": row["platform_id"],
            "display_name": row["display_name"],
            "reason": row["reason"],
            "status": row["status"],
            "from_name": row["from_name"],
            "note": row["note"],
        }
        JsonViewDialog("綁定層級資訊", payload, self).exec()
