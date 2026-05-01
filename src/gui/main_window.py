from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QMenuBar,
    QSplitter,
)

from core.config import LOGGER
from core.controller import Controller
from .dialogs import (
    AddCreatorDialog,
    AddNameDialog,
    AddPostDialog,
    AddUrlDialog,
    AddWorkDialog,
    SetReminderDialog,
)
from .notifications import NotificationManager, NotificationPayload
from .panels import JobsPanel
from .pubsub_bridge import QtPubSubBridge
from .resources import app_icon
from .tabs import (
    AliasesTab,
    OverviewTab,
    PostsTab,
    ReminderTab,
    UrlsTab,
    WorklogsTab,
)
from .themes import ThemeName, apply_theme, available_themes
from .widgets import RightNotebook, SearchPane


class ClogMainWindow(QMainWindow):
    def __init__(self, controller: Controller, bridge: QtPubSubBridge) -> None:
        super().__init__()
        self.setWindowTitle("clog")
        self.setWindowIcon(app_icon())
        self.resize(1200, 720)
        self._controller = controller
        self._bridge = bridge
        self._notifications = NotificationManager(self)

        self._jobs_panel = JobsPanel(controller)
        self._build_menubar()
        self._build_central()
        self._build_status_bar()
        self._wire_pubsub()

    # ── boot ─────────────────────────────────────────────────────────────
    def _build_menubar(self) -> None:
        bar: QMenuBar = self.menuBar()

        add_menu = bar.addMenu("新增")
        act_add_creator = QAction("新增創作者", self)
        act_add_creator.setShortcut(QKeySequence("Ctrl+N"))
        act_add_creator.triggered.connect(self._open_add_creator)
        add_menu.addAction(act_add_creator)

        network_menu = bar.addMenu("網路")
        act_jobs = QAction("後台工作", self)
        act_jobs.setShortcut(QKeySequence("Ctrl+J"))
        act_jobs.triggered.connect(self._open_jobs_panel)
        network_menu.addAction(act_jobs)

        settings_menu = bar.addMenu("設定")
        theme_menu = QMenu("介面主題", self)
        for name in available_themes():
            label = "預設" if name == "light" else "黑暗" if name == "dark" else name.capitalize()
            t_act = QAction(label, theme_menu)
            t_act.triggered.connect(lambda _checked=False, n=name: self._switch_theme(n))
            theme_menu.addAction(t_act)
        settings_menu.addMenu(theme_menu)

    def _build_central(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        self._search = SearchPane(self._controller, splitter)
        self._search.creatorActivated.connect(self._on_creator_activated)
        self._search.contextRequested.connect(self._on_search_context_requested)

        self._notebook = RightNotebook(self._controller, splitter)
        self._register_tab_factories()

        splitter.addWidget(self._search)
        splitter.addWidget(self._notebook)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([400, 800])
        self.setCentralWidget(splitter)
        self._notifications.bind_anchor_widget()

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        bar.showMessage(f"db: {self._controller.db_path}")

    def _wire_pubsub(self) -> None:
        for action in (
            "add_creator",
            "add_name",
            "add_url",
            "add_post",
            "set_reminder",
            "add_work",
            "refetch_post_meta",
        ):
            self._controller.pubsub.sub(self, "_on_data_changed", f"data.{action}")
        self._controller.pubsub.sub(self, "_on_data_changed", "data.changed")
        self._controller.pubsub.sub(self, "_on_message", "message")
        self._controller.pubsub.sub(self, "_on_queue_updated", "queue.updated")

    # ── pubsub handlers ────────────────────────────────────────────────────
    def _on_message(self, payload: NotificationPayload | dict[str, Any] | str) -> None:
        normalized = NotificationPayload.from_any(payload)
        if normalized.level == "info":
            if normalized.detail:
                LOGGER.info("%s: %s | detail=%s", normalized.title, normalized.text, normalized.detail)
            else:
                LOGGER.info("%s: %s", normalized.title, normalized.text)
            return
        self._notifications.add_message(normalized)

    def _on_data_changed(self, **_kwargs: Any) -> None:
        self._refresh_views()

    def _on_queue_updated(self, **_kwargs: Any) -> None:
        if self._jobs_panel.isVisible():
            self._jobs_panel.refresh()

    def _refresh_views(self) -> None:
        try:
            self._search.refresh()
            self._notebook.refresh_all_tabs()
        except Exception as exc:
            LOGGER.exception("refresh failed: %s", exc)
            self._notify("Refresh Failed", str(exc), level="error", detail=repr(exc), sticky=True)

    # ── search interactions ────────────────────────────────────────────────
    def _on_creator_activated(self, creator_id: int, display_name: str) -> None:
        self._notebook.remember_creator_name(creator_id, display_name)
        self._notebook.switch_or_open("Overview", creator_id)

    def _on_search_context_requested(self, creator_id: int, global_pos: QPoint, display_name: str) -> None:
        self._notebook.remember_creator_name(creator_id, display_name)
        target = {"creator_id": creator_id, "display_name": display_name}
        menu = QMenu(self)
        menu.addAction("新增別名", lambda: AddNameDialog(self._controller, self, locked_target=target).exec())
        menu.addAction("新增網址", lambda: AddUrlDialog(self._controller, self, locked_target=target).exec())
        menu.addAction("新增貼文", lambda: AddPostDialog(self._controller, self, locked_target=target).exec())
        menu.addAction("新增工作紀錄", lambda: AddWorkDialog(self._controller, self, locked_target=target).exec())
        menu.addAction("設定提醒", lambda: SetReminderDialog(self._controller, self, locked_target=target).exec())
        menu.exec(global_pos)

    # ── factory wiring ─────────────────────────────────────────────────────
    def _register_tab_factories(self) -> None:
        nb = self._notebook

        def overview_factory(controller, cid):
            return OverviewTab(
                controller,
                cid,
                navigator=self._navigate_from_overview,
            )

        def make_maintenance_factory(cls):
            def factory(controller, cid):
                return cls(
                    controller,
                    cid,
                    creator_display_name=nb.get_display_name(cid),
                )
            return factory

        nb.register_factory("Overview", overview_factory)
        nb.register_factory("URLs", make_maintenance_factory(UrlsTab))
        nb.register_factory("Aliases", make_maintenance_factory(AliasesTab))
        nb.register_factory("Posts", make_maintenance_factory(PostsTab))
        nb.register_factory("Reminder", make_maintenance_factory(ReminderTab))
        nb.register_factory("Worklogs", make_maintenance_factory(WorklogsTab))

    # ── overview navigation ────────────────────────────────────────────────
    def _navigate_from_overview(self, kind: str, creator_id: int, **focus_kwargs: Any) -> None:
        self._notebook.switch_or_open(
            kind,
            creator_id,
            **focus_kwargs,
        )

    # ── dialog launchers ───────────────────────────────────────────────────
    def _open_add_creator(self) -> None:
        AddCreatorDialog(self._controller, self).exec()

    def _open_jobs_panel(self) -> None:
        self._jobs_panel.show()
        self._jobs_panel.raise_()
        self._jobs_panel.activateWindow()

    # ── theme switch ───────────────────────────────────────────────────────
    def _switch_theme(self, name: ThemeName) -> None:
        app = QApplication.instance()
        if app is None:
            return
        apply_theme(app, name)
        self._controller.set_setting("user", "theme", name)
        self.statusBar().showMessage(f"theme: {name}", 3000)
        self._notify("Theme Updated", f"Switched theme to {name}.", level="success")

    # ── helpers ────────────────────────────────────────────────────────────
    def _notify(
        self,
        title: str,
        text: str,
        *,
        level: str = "info",
        detail: str | None = None,
        sticky: bool = True,
        timeout_ms: int = 0,
    ) -> None:
        self._controller.pubsub.pub(
            "message",
            payload=NotificationPayload(
                title=title,
                text=text,
                level=level,
                detail=detail,
                sticky=sticky,
                timeout_ms=timeout_ms,
            ),
        )

    # ── lifecycle ──────────────────────────────────────────────────────────
    def event(self, event: QEvent) -> bool:
        if event.type() in (
            QEvent.Type.KeyPress,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.Wheel,
        ):
            self._controller.record_user_activity()
        return super().event(event)

    def closeEvent(self, event) -> None:
        self._bridge.shutdown()
        super().closeEvent(event)
