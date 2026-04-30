"""Posts maintenance tab — list + edit/delete + meta history (spec §10.4)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.config import LOGGER
from ..dialogs import AddPostDialog, EditPostDialog, JsonViewDialog
from .base_maintenance_tab import BaseMaintenanceTab


class PostsTab(BaseMaintenanceTab):
    POST_HEADERS = ["#", "platform", "標題 / URL", "貼文時間", "擷取時間"]

    def __init__(self, *args, **kwargs) -> None:
        self._meta_visible = False
        self._orientation = Qt.Orientation.Horizontal
        self._current_post: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)

    def _build_body(self, layout: QVBoxLayout) -> None:
        self.add_toolbar_button("水平/垂直", self._toggle_orientation)
        self._meta_button = self.add_toolbar_button("顯示 meta history", self._toggle_meta_history)

        self._splitter = QSplitter(self._orientation)

        # ── posts table ───────────────────────────────────────────────────
        self._posts_table = QTableWidget(0, len(self.POST_HEADERS))
        self._posts_table.setHorizontalHeaderLabels(self.POST_HEADERS)
        self._posts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._posts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._posts_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._posts_table.verticalHeader().setVisible(False)
        self._posts_table.setAlternatingRowColors(True)
        header = self._posts_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._posts_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._posts_table.customContextMenuRequested.connect(self._on_post_context_menu)
        self._posts_table.itemSelectionChanged.connect(self._on_post_selected)
        self._splitter.addWidget(self._posts_table)

        # ── meta history list ─────────────────────────────────────────────
        self._meta_list = QListWidget()
        self._meta_list.setAlternatingRowColors(True)
        self._meta_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._meta_list.customContextMenuRequested.connect(self._on_meta_context_menu)
        self._meta_list.itemDoubleClicked.connect(self._on_meta_double_click)
        self._meta_list.setVisible(False)
        self._splitter.addWidget(self._meta_list)

        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        layout.addWidget(self._splitter, 1)

    # ── refresh ────────────────────────────────────────────────────────────
    def _run_refresh(self) -> None:
        posts = self._controller.read("creator_posts", self._creator_id)
        table = self._posts_table
        table.setRowCount(0)
        for row in posts:
            r = table.rowCount()
            table.insertRow(r)
            post_id = int(row["id"])
            platform = row["platform"] or ""
            if row["platform_id"]:
                platform = f"{platform}:{row['platform_id']}"
            title_or_url = row["title"] or row["url"] or ""
            posted = str(row["posted_at"] or "")[:19]
            captured = str(row["captured_at"] or "")[:19]
            cells = [f"#{post_id}", platform, title_or_url, posted, captured]
            for col, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, dict(row))
                item.setToolTip(value)
                table.setItem(r, col, item)
        table.resizeColumnsToContents()
        if posts:
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        # Refresh meta history if a row is currently selected
        if self._current_post is not None:
            self._refresh_meta_history(int(self._current_post["id"]))

    def _refresh_meta_history(self, post_id: int) -> None:
        rows = self._controller.read("post_meta_history", post_id)
        self._meta_list.clear()
        for row in rows:
            captured = str(row["captured_at"] or "")[:19]
            text = f"#{row['id']}  {row['source']}  {captured}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, dict(row))
            self._meta_list.addItem(item)

    # ── selection ──────────────────────────────────────────────────────────
    def _selected_post(self) -> dict[str, Any] | None:
        r = self._posts_table.currentRow()
        if r < 0:
            return None
        item = self._posts_table.item(r, 0)
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _on_post_selected(self) -> None:
        row = self._selected_post()
        self._current_post = row
        if self._meta_visible and row is not None:
            self._refresh_meta_history(int(row["id"]))

    # ── orientation / meta visibility ──────────────────────────────────────
    def _toggle_orientation(self) -> None:
        self._orientation = (
            Qt.Orientation.Vertical
            if self._orientation == Qt.Orientation.Horizontal
            else Qt.Orientation.Horizontal
        )
        self._splitter.setOrientation(self._orientation)

    def _toggle_meta_history(self) -> None:
        self._meta_visible = not self._meta_visible
        self._meta_list.setVisible(self._meta_visible)
        self._meta_button.setText(
            "隱藏 meta history" if self._meta_visible else "顯示 meta history"
        )
        if self._meta_visible and self._current_post is not None:
            self._refresh_meta_history(int(self._current_post["id"]))

    # ── add/edit/delete ────────────────────────────────────────────────────
    def _open_add_dialog(self) -> None:
        AddPostDialog(self._controller, self, locked_target=self.locked_target).exec()

    def _on_post_context_menu(self, pos: QPoint) -> None:
        index = self._posts_table.indexAt(pos)
        if not index.isValid():
            return
        self._posts_table.selectRow(index.row())
        row = self._selected_post()
        if row is None:
            return
        menu = QMenu(self)
        menu.addAction("編輯", lambda: self._edit_post(row))
        menu.addAction("刪除", lambda: self._delete_post(row))
        menu.addSeparator()
        menu.addAction("複製貼文網址", lambda: self._copy_post_url(row))
        menu.addAction("顯示詳細資訊", lambda: self._show_post_detail(row))
        menu.addAction("重新取得資訊", lambda: self._refetch_post_meta(row))
        menu.exec(self._posts_table.viewport().mapToGlobal(pos))

    def _edit_post(self, row: dict[str, Any]) -> None:
        EditPostDialog(
            self._controller,
            self,
            post_id=int(row["id"]),
            url=row["url"],
            note=row["note"],
            title_text=row["title"],
            platform_post_id=row["platform_post_id"],
            locked_target=self.locked_target,
        ).exec()

    def _delete_post(self, row: dict[str, Any]) -> None:
        confirm = QMessageBox.question(
            self,
            "刪除貼文",
            f"確定要刪除這則貼文？\n\n{row['url']}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self._controller.write("delete_post", int(row["id"]))
        except Exception as exc:
            LOGGER.exception("delete_post failed: %s", exc)
            QMessageBox.warning(self, "刪除失敗", str(exc))

    def _copy_post_url(self, row: dict[str, Any]) -> None:
        QGuiApplication.clipboard().setText(row["url"] or "")

    def _show_post_detail(self, row: dict[str, Any]) -> None:
        try:
            detail = self._controller.read("post_detail", int(row["id"]))
            payload = dict(detail)
        except Exception as exc:
            LOGGER.exception("post_detail failed: %s", exc)
            payload = dict(row)
        JsonViewDialog(f"貼文 #{row['id']} 詳細資訊", payload, self).exec()

    def _refetch_post_meta(self, row: dict[str, Any]) -> None:
        try:
            self._controller.write("refetch_post_meta", int(row["id"]))
        except Exception as exc:
            LOGGER.exception("refetch_post_meta failed: %s", exc)
            QMessageBox.warning(self, "重新取得資訊失敗", str(exc))
            return
        if self._meta_visible:
            self._refresh_meta_history(int(row["id"]))

    # ── meta history ───────────────────────────────────────────────────────
    def _selected_meta(self) -> dict[str, Any] | None:
        item = self._meta_list.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _on_meta_double_click(self, item: QListWidgetItem) -> None:
        row = item.data(Qt.ItemDataRole.UserRole)
        if row is None:
            return
        JsonViewDialog(
            f"meta #{row['id']}  {row['captured_at']}",
            row.get("raw_json") or "",
            self,
        ).exec()

    def _on_meta_context_menu(self, pos: QPoint) -> None:
        item = self._meta_list.itemAt(pos)
        if item is None:
            return
        self._meta_list.setCurrentItem(item)
        row = item.data(Qt.ItemDataRole.UserRole)
        if row is None:
            return
        menu = QMenu(self)
        menu.addAction("刪除", lambda: self._delete_meta(row))
        menu.exec(self._meta_list.viewport().mapToGlobal(pos))

    def _delete_meta(self, row: dict[str, Any]) -> None:
        confirm = QMessageBox.question(
            self,
            "刪除 meta 紀錄",
            f"確定要刪除 meta 歷史 #{row['id']}？\n（此操作不會影響其他資料）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self._controller.write("delete_post_meta_history", int(row["id"]))
        except Exception as exc:
            LOGGER.exception("delete meta failed: %s", exc)
            QMessageBox.warning(self, "刪除失敗", str(exc))
            return
        if self._current_post is not None:
            self._refresh_meta_history(int(self._current_post["id"]))
