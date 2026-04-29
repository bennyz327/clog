from __future__ import annotations

import sqlite3
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.constants import AmbiguousTarget, UserError
from core.config import LOGGER
from core.controller import Controller
from core.render import (
    render_creator_snapshot,
    render_post_detail,
    render_work_detail,
)
from .dialogs import (
    AddCreatorDialog,
    AddNameDialog,
    AddPostDialog,
    AddUrlDialog,
    AddWorkDialog,
    SetReminderDialog,
)
from .pubsub_bridge import QtPubSubBridge
from .themes import ThemeName, apply_theme, available_themes

DEFAULT_RECENT_LIMIT = 50


class ClogMainWindow(QMainWindow):
    def __init__(self, controller: Controller, bridge: QtPubSubBridge) -> None:
        super().__init__()
        self.setWindowTitle("clog")
        self.resize(1200, 720)
        self._controller = controller
        self._bridge = bridge
        self._current_creator_id: int | None = None
        self._search_groups: list[dict[str, Any]] = []

        self._build_toolbar()
        self._build_central()
        self._build_status_bar()
        self._setup_shortcuts()
        self._wire_pubsub()
        self.refresh_all()

    # ── boot ─────────────────────────────────────────────────────────────
    def _build_toolbar(self) -> None:
        bar = QToolBar("Main", self)
        bar.setMovable(False)
        self.addToolBar(bar)

        def add_action(text: str, shortcut: str, slot: Callable[[], None]) -> QAction:
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
                act.setToolTip(f"{text} ({shortcut})")
            act.triggered.connect(slot)
            bar.addAction(act)
            return act

        add_action("Add Creator", "F1", self._open_add_creator)
        add_action("Add Name", "F2", self._open_add_name)
        add_action("Add URL", "F3", self._open_add_url)
        add_action("Add Post", "F4", self._open_add_post)
        add_action("Set Reminder", "F5", self._open_set_reminder)
        add_action("Add Work", "F6", self._open_add_work)
        bar.addSeparator()

        refresh = QAction("Refresh", self)
        refresh.triggered.connect(self.refresh_all)
        bar.addAction(refresh)

        theme_menu = QMenu("Theme", self)
        for name in available_themes():
            t_act = QAction(name.capitalize(), theme_menu)
            t_act.triggered.connect(lambda _checked=False, n=name: self._switch_theme(n))
            theme_menu.addAction(t_act)
        theme_act = QAction("Theme", self)
        theme_act.setMenu(theme_menu)
        bar.addAction(theme_act)

    def _build_central(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # ── sidebar ──
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(6, 6, 6, 6)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("search creators / aliases / profiles / posts ... (Enter)")
        self._search_input.returnPressed.connect(self._run_search)
        sidebar_layout.addWidget(self._search_input)

        self._creators_table = self._make_table(
            ["#id", "name", "status", "updated"],
            on_select=self._on_creators_selection,
        )
        self._creators_table.customContextMenuRequested.connect(self._creators_context_menu)
        sidebar_layout.addWidget(self._creators_table, 1)

        # ── center tabs ──
        self._tabs = QTabWidget()
        self._search_table = self._make_table(
            ["#", "creator", "matches", "snippets"],
            on_select=self._on_search_selection,
        )
        self._tabs.addTab(self._wrap_table(self._search_table), "Search")

        self._posts_table = self._make_table(
            ["post", "creator", "title", "captured"],
            on_select=self._on_post_selection,
        )
        self._tabs.addTab(self._wrap_table(self._posts_table), "Recent posts")

        self._works_table = self._make_table(
            ["work", "creator", "content", "created"],
            on_select=self._on_work_selection,
        )
        self._tabs.addTab(self._wrap_table(self._works_table), "Recent works")

        self._due_table = self._make_table(
            ["creator", "due", "interval", "note"],
            on_select=self._on_due_selection,
        )
        self._tabs.addTab(self._wrap_table(self._due_table), "Due reminders")

        # ── detail pane ──
        detail_holder = QWidget()
        detail_layout = QVBoxLayout(detail_holder)
        detail_layout.setContentsMargins(6, 6, 6, 6)
        self._detail_label = QLabel("(select an item)")
        self._detail_label.setStyleSheet("font-weight: bold;")
        detail_layout.addWidget(self._detail_label)
        self._detail_view = QPlainTextEdit()
        self._detail_view.setReadOnly(True)
        detail_layout.addWidget(self._detail_view, 1)

        splitter.addWidget(sidebar)
        splitter.addWidget(self._tabs)
        splitter.addWidget(detail_holder)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([280, 480, 440])
        self.setCentralWidget(splitter)

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        bar.showMessage(f"db: {self._controller.db_path}")

    def _setup_shortcuts(self) -> None:
        # F1–F6 are wired through QAction shortcuts on the toolbar.
        pass

    def _wire_pubsub(self) -> None:
        bridge = self._bridge
        for action in (
            "add_creator",
            "add_name",
            "add_url",
            "add_post",
            "set_reminder",
            "add_work",
        ):
            bridge.sub(f"data.{action}", lambda **_kw: self.refresh_all())
        bridge.sub("enrichment.completed", lambda **_kw: self._on_enrichment_completed())

    # ── helpers ──────────────────────────────────────────────────────────
    def _make_table(
        self,
        headers: list[str],
        *,
        on_select: Callable[[QTableWidget], None],
    ) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.itemSelectionChanged.connect(lambda t=table, fn=on_select: fn(t))
        return table

    def _wrap_table(self, table: QTableWidget) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(table)
        return w

    @staticmethod
    def _set_row(table: QTableWidget, row: int, cells: list[Any], *, key: Any = None) -> None:
        while table.columnCount() < len(cells):
            table.insertColumn(table.columnCount())
        for col, value in enumerate(cells):
            text = "" if value is None else str(value)
            item = QTableWidgetItem(text)
            if col == 0 and key is not None:
                item.setData(Qt.ItemDataRole.UserRole, key)
            table.setItem(row, col, item)

    @staticmethod
    def _selected_key(table: QTableWidget) -> Any:
        row = table.currentRow()
        if row < 0:
            return None
        item = table.item(row, 0)
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    # ── refresh ──────────────────────────────────────────────────────────
    def refresh_all(self) -> None:
        try:
            self._refresh_creators()
            self._refresh_recent_posts()
            self._refresh_recent_works()
            self._refresh_due()
            if self._tabs.currentWidget() is not None and self._search_input.text().strip():
                self._run_search(silent=True)
        except Exception as exc:
            LOGGER.exception("Refresh failed: %s", exc)

    def _refresh_creators(self) -> None:
        rows = self._controller.read("recent_creators", DEFAULT_RECENT_LIMIT, 0)
        table = self._creators_table
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            self._set_row(
                table,
                r,
                [
                    f"#{row['id']}",
                    row["primary_name"],
                    row["status"],
                    row["updated_at"],
                ],
                key=int(row["id"]),
            )
        table.resizeColumnsToContents()
        if self._current_creator_id is not None:
            self._reselect_creator(self._current_creator_id)

    def _refresh_recent_posts(self) -> None:
        rows = self._controller.read("recent_posts", DEFAULT_RECENT_LIMIT, 0)
        table = self._posts_table
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            self._set_row(
                table,
                r,
                [
                    f"post:{row['id']}",
                    f"#{row['creator_id']} {row['primary_name']}",
                    row["title"] or row["url"],
                    row["captured_at"],
                ],
                key=int(row["id"]),
            )
        table.resizeColumnsToContents()

    def _refresh_recent_works(self) -> None:
        rows = self._controller.read("recent_worklogs", DEFAULT_RECENT_LIMIT, 0)
        table = self._works_table
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            self._set_row(
                table,
                r,
                [
                    f"work:{row['id']}",
                    f"#{row['creator_id']} {row['primary_name']}",
                    (row["content"] or "").splitlines()[0][:120] if row["content"] else "",
                    row["created_at"],
                ],
                key=int(row["id"]),
            )
        table.resizeColumnsToContents()

    def _refresh_due(self) -> None:
        rows = self._controller.read("due", include_future=True)
        table = self._due_table
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            self._set_row(
                table,
                r,
                [
                    f"#{row['creator_id']} {row['primary_name']}",
                    row["next_due_at"],
                    row["interval_days"] or "",
                    row["note"] or "",
                ],
                key=int(row["creator_id"]),
            )
        table.resizeColumnsToContents()

    def _reselect_creator(self, creator_id: int) -> None:
        table = self._creators_table
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == creator_id:
                table.selectRow(r)
                return

    # ── selection handlers ─────────────────────────────────────────────
    def _on_creators_selection(self, table: QTableWidget) -> None:
        creator_id = self._selected_key(table)
        if not isinstance(creator_id, int):
            return
        self._current_creator_id = creator_id
        try:
            snapshot = self._controller.read("creator_snapshot", creator_id)
        except Exception as exc:
            LOGGER.exception("creator_snapshot failed: %s", exc)
            return
        self._show_creator_detail(snapshot)

    def _on_search_selection(self, table: QTableWidget) -> None:
        creator_id = self._selected_key(table)
        if not isinstance(creator_id, int):
            return
        self._current_creator_id = creator_id
        snapshot = self._controller.read("creator_snapshot", creator_id)
        self._show_creator_detail(snapshot)
        self._reselect_creator(creator_id)

    def _on_post_selection(self, table: QTableWidget) -> None:
        post_id = self._selected_key(table)
        if not isinstance(post_id, int):
            return
        row = self._controller.read("post_detail", post_id)
        self._detail_label.setText(f"post:{post_id}")
        self._detail_view.setPlainText(render_post_detail(row))

    def _on_work_selection(self, table: QTableWidget) -> None:
        work_id = self._selected_key(table)
        if not isinstance(work_id, int):
            return
        row = self._controller.read("work_detail", work_id)
        self._detail_label.setText(f"work:{work_id}")
        self._detail_view.setPlainText(render_work_detail(row))

    def _on_due_selection(self, table: QTableWidget) -> None:
        creator_id = self._selected_key(table)
        if not isinstance(creator_id, int):
            return
        self._current_creator_id = creator_id
        snapshot = self._controller.read("creator_snapshot", creator_id)
        self._show_creator_detail(snapshot)
        self._reselect_creator(creator_id)

    def _show_creator_detail(self, snapshot: dict[str, Any]) -> None:
        creator = snapshot["creator"]
        self._detail_label.setText(f"#{creator['id']} {creator['primary_name']}")
        self._detail_view.setPlainText(render_creator_snapshot(snapshot))

    # ── search ───────────────────────────────────────────────────────────
    def _run_search(self, silent: bool = False) -> None:
        query = self._search_input.text().strip()
        if not query:
            self._search_table.setRowCount(0)
            self._search_groups = []
            return
        try:
            groups: list[dict[str, Any]] = self._controller.read("grouped_search", query)
        except Exception as exc:
            LOGGER.exception("search failed: %s", exc)
            if not silent:
                QMessageBox.critical(self, "Search", str(exc))
            return
        self._search_groups = groups
        table = self._search_table
        table.setRowCount(0)
        for index, group in enumerate(groups, start=1):
            creator = group["creator"]
            matches = ", ".join(
                f"{kind}{('×' + str(c)) if c > 1 else ''}"
                for kind, c in group.get("kind_counts", {}).items()
                if c
            )
            snippets = "; ".join(group.get("snippets", []) or [])
            r = table.rowCount()
            table.insertRow(r)
            self._set_row(
                table,
                r,
                [
                    str(index),
                    f"#{creator['id']} {creator['primary_name']}",
                    matches,
                    snippets,
                ],
                key=int(creator["id"]),
            )
        table.resizeColumnsToContents()
        self._tabs.setCurrentIndex(0)
        if not silent:
            self.statusBar().showMessage(f"search: {len(groups)} group(s) for {query!r}", 5000)

    # ── enrichment ─────────────────────────────────────────────────────
    def _on_enrichment_completed(self) -> None:
        self.statusBar().showMessage("metadata enrichment finished", 4000)
        self._refresh_creators()
        if self._current_creator_id is not None:
            try:
                snapshot = self._controller.read("creator_snapshot", self._current_creator_id)
                self._show_creator_detail(snapshot)
            except Exception:
                pass

    # ── context menus ──────────────────────────────────────────────────
    def _creators_context_menu(self, pos) -> None:
        row = self._creators_table.indexAt(pos).row()
        if row < 0:
            return
        item = self._creators_table.item(row, 0)
        if item is None:
            return
        creator_id = item.data(Qt.ItemDataRole.UserRole)
        target = f"#{creator_id}"
        menu = QMenu(self)
        menu.addAction("Show", lambda: self._on_creators_selection(self._creators_table))
        menu.addAction("Add Name (F2)", lambda: self._open_add_name(default_target=target))
        menu.addAction("Add URL (F3)", lambda: self._open_add_url(default_target=target))
        menu.addAction("Add Post (F4)", lambda: self._open_add_post(default_target=target))
        menu.addAction("Set Reminder (F5)", lambda: self._open_set_reminder(default_target=target))
        menu.addAction("Add Work (F6)", lambda: self._open_add_work(default_target=target))
        menu.exec(self._creators_table.viewport().mapToGlobal(pos))

    # ── dialog launchers ───────────────────────────────────────────────
    def _open_add_creator(self) -> None:
        AddCreatorDialog(self._controller, self).exec()

    def _open_add_name(self, *, default_target: str | None = None) -> None:
        AddNameDialog(self._controller, self, default_target=self._target_or_current(default_target)).exec()

    def _open_add_url(self, *, default_target: str | None = None) -> None:
        AddUrlDialog(self._controller, self, default_target=self._target_or_current(default_target)).exec()

    def _open_add_post(self, *, default_target: str | None = None) -> None:
        AddPostDialog(self._controller, self, default_target=self._target_or_current(default_target)).exec()

    def _open_set_reminder(self, *, default_target: str | None = None) -> None:
        SetReminderDialog(self._controller, self, default_target=self._target_or_current(default_target)).exec()

    def _open_add_work(self, *, default_target: str | None = None) -> None:
        AddWorkDialog(self._controller, self, default_target=self._target_or_current(default_target)).exec()

    def _target_or_current(self, explicit: str | None) -> str | None:
        if explicit:
            return explicit
        if self._current_creator_id is not None:
            return f"#{self._current_creator_id}"
        return None

    # ── theme switch ───────────────────────────────────────────────────
    def _switch_theme(self, name: ThemeName) -> None:
        app = QApplication.instance()
        if app is None:
            return
        apply_theme(app, name)
        self._controller.options["theme"] = name
        self.statusBar().showMessage(f"theme: {name}", 3000)

    # ── lifecycle ──────────────────────────────────────────────────────
    def closeEvent(self, event) -> None:
        self._bridge.shutdown()
        super().closeEvent(event)
