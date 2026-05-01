from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.background import progress_text
from core.background_handlers import list_job_types
from core.config import LOGGER
from core.utils import json_loads

if TYPE_CHECKING:
    from core.controller import Controller


QUEUE_STATUSES = ("queued", "running")
HISTORY_STATUSES = ("succeeded", "failed", "cancelled")
HEADERS = ["ID", "狀態", "類型", "標題", "來源", "進度", "建立", "開始", "結束"]

STATUS_LABELS = {
    "queued": "等待中",
    "running": "執行中",
    "succeeded": "已完成",
    "failed": "失敗",
    "cancelled": "已取消",
}

SOURCE_LABELS = {
    "gui": "圖形介面",
    "cli": "命令列",
    "system": "系統",
}

JOB_TYPE_LABELS = {
    "command.add_creator_with_url": "新增創作者（含網址）",
    "command.add_name_with_url": "新增名稱（含網址）",
    "command.add_url": "新增網址",
    "command.add_post": "新增貼文",
    "command.refetch_post_meta": "重新擷取貼文資料",
    "profile.resolve_metadata": "補全創作者中繼資料",
}

EVENT_TYPE_LABELS = {
    "enqueued": "已加入佇列",
    "running": "開始執行",
    "succeeded": "執行完成",
    "failed": "執行失敗",
    "requeued": "重新排入佇列",
    "cancel_requested": "已請求取消",
    "terminate_requested": "已請求終止",
    "cancelled": "已取消",
    "retry_requested": "已請求重試",
    "recovered_stale": "已回收逾時任務",
}


def _fmt_ts(value: Any) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)


def _job_type_label(job_type: str) -> str:
    return JOB_TYPE_LABELS.get(job_type, job_type)


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def _source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def _progress_label(row: Any, live_snapshot: Any | None) -> str:
    if live_snapshot is not None:
        numeric = progress_text(live_snapshot.progress_current, live_snapshot.progress_total)
        text = (live_snapshot.status_text or "").strip()
        if text and numeric != "—":
            return f"{numeric} {text}"
        if text:
            return text
        if numeric != "—":
            return numeric

    status = str(row["status"])
    if status == "queued":
        return "等待處理"
    if status == "running":
        return "執行中"
    if status == "succeeded":
        return "已完成"
    if status == "failed":
        return "執行失敗"
    if status == "cancelled":
        return "已取消"
    return "-"


class _JobTab:
    def __init__(self, tab_label: str, statuses: tuple[str, ...]) -> None:
        self.tab_label = tab_label
        self.statuses = statuses
        self.widget = QWidget()
        self.status_filter = QComboBox()
        self.type_filter = QComboBox()
        self.source_filter = QComboBox()
        self.table = QTableWidget(0, len(HEADERS))
        self.summary = QLabel("目前顯示 0 筆任務")
        self.refresh_button = QPushButton("重新整理")


class JobsPanel(QWidget):
    def __init__(self, controller: "Controller", parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._controller = controller
        self.setWindowTitle("後台工作")
        self.resize(1120, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._tabs = QTabWidget(self)
        root.addWidget(self._tabs, 1)

        self._queue_tab = _JobTab("佇列", QUEUE_STATUSES)
        self._history_tab = _JobTab("歷史", HISTORY_STATUSES)
        self._build_job_tab(self._queue_tab)
        self._build_job_tab(self._history_tab)
        self._build_processing_tab()

        self._tabs.addTab(self._queue_tab.widget, "佇列")
        self._tabs.addTab(self._history_tab.widget, "歷史")
        self._tabs.addTab(self._processing_widget, "處理設定")

        self._wire_actions()
        self._load_processing_settings()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(500)
        self._refresh_timer.timeout.connect(self._refresh_visible)
        self._refresh_timer.start()

        self.refresh()

    def refresh(self) -> None:
        self._refresh_job_tab(self._queue_tab)
        self._refresh_job_tab(self._history_tab)

    def _refresh_visible(self) -> None:
        if self.isVisible():
            self.refresh()

    def _build_job_tab(self, tab: _JobTab) -> None:
        root = QVBoxLayout(tab.widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        filter_box = QGroupBox("篩選條件")
        filter_layout = QGridLayout(filter_box)
        filter_layout.setContentsMargins(12, 12, 12, 12)
        filter_layout.setHorizontalSpacing(10)
        filter_layout.setVerticalSpacing(8)
        filter_layout.addWidget(QLabel("狀態"), 0, 0)
        filter_layout.addWidget(tab.status_filter, 0, 1)
        filter_layout.addWidget(QLabel("類型"), 0, 2)
        filter_layout.addWidget(tab.type_filter, 0, 3)
        filter_layout.addWidget(QLabel("來源"), 0, 4)
        filter_layout.addWidget(tab.source_filter, 0, 5)
        filter_layout.addWidget(tab.refresh_button, 0, 6)
        filter_layout.setColumnStretch(1, 1)
        filter_layout.setColumnStretch(3, 2)
        filter_layout.setColumnStretch(5, 1)
        root.addWidget(filter_box)

        tab.status_filter.addItem("全部", None)
        for status in tab.statuses:
            tab.status_filter.addItem(_status_label(status), status)

        tab.type_filter.addItem("全部", None)
        for job_type in list_job_types():
            tab.type_filter.addItem(_job_type_label(job_type), job_type)

        tab.source_filter.addItem("全部", None)
        for source in ("gui", "cli", "system"):
            tab.source_filter.addItem(_source_label(source), source)

        tab.table.setHorizontalHeaderLabels(HEADERS)
        tab.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tab.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tab.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tab.table.verticalHeader().setVisible(False)
        tab.table.setAlternatingRowColors(True)
        tab.table.setShowGrid(False)
        tab.table.setWordWrap(False)
        tab.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab.table.verticalHeader().setDefaultSectionSize(30)
        tab.table.horizontalHeader().setMinimumHeight(36)
        header = tab.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(tab.table, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(4, 0, 4, 0)
        footer.addWidget(tab.summary)
        footer.addStretch(1)
        footer.addWidget(QLabel("對任務按右鍵可查看詳細資料與操作"))
        root.addLayout(footer)

    def _build_processing_tab(self) -> None:
        self._processing_widget = QWidget()
        root = QVBoxLayout(self._processing_widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        general = QGroupBox("背景執行")
        general_form = QFormLayout(general)
        general_form.setContentsMargins(12, 12, 12, 12)
        general_form.setSpacing(8)
        self._run_idle = QCheckBox("允許閒置時執行")
        self._run_active = QCheckBox("允許使用中執行")
        self._idle_threshold = QSpinBox()
        self._idle_threshold.setRange(1, 3600)
        self._idle_threshold.setSuffix(" 秒")
        self._poll_interval = QSpinBox()
        self._poll_interval.setRange(100, 10000)
        self._poll_interval.setSuffix(" 毫秒")
        self._global_concurrency = QSpinBox()
        self._global_concurrency.setRange(1, 32)
        general_form.addRow("閒置模式", self._run_idle)
        general_form.addRow("使用中模式", self._run_active)
        general_form.addRow("閒置判定時間", self._idle_threshold)
        general_form.addRow("輪詢間隔", self._poll_interval)
        general_form.addRow("全域併發", self._global_concurrency)
        root.addWidget(general)

        throttling = QGroupBox("節流")
        throttle_grid = QGridLayout(throttling)
        throttle_grid.setContentsMargins(12, 12, 12, 12)
        throttle_grid.setHorizontalSpacing(10)
        throttle_grid.setVerticalSpacing(8)
        self._active_packet = QSpinBox()
        self._active_packet.setRange(100, 10000)
        self._active_packet.setSuffix(" 毫秒")
        self._active_rest = QSpinBox()
        self._active_rest.setRange(0, 1000)
        self._active_rest.setSuffix(" %")
        self._idle_packet = QSpinBox()
        self._idle_packet.setRange(100, 10000)
        self._idle_packet.setSuffix(" 毫秒")
        self._idle_rest = QSpinBox()
        self._idle_rest.setRange(0, 1000)
        self._idle_rest.setSuffix(" %")
        throttle_grid.addWidget(QLabel(""), 0, 0)
        throttle_grid.addWidget(QLabel("處理批次時間"), 0, 1)
        throttle_grid.addWidget(QLabel("休息比例"), 0, 2)
        throttle_grid.addWidget(QLabel("使用中"), 1, 0)
        throttle_grid.addWidget(self._active_packet, 1, 1)
        throttle_grid.addWidget(self._active_rest, 1, 2)
        throttle_grid.addWidget(QLabel("閒置"), 2, 0)
        throttle_grid.addWidget(self._idle_packet, 2, 1)
        throttle_grid.addWidget(self._idle_rest, 2, 2)
        root.addWidget(throttling)

        slots = QGroupBox("工作類型併發上限")
        slots_form = QFormLayout(slots)
        slots_form.setContentsMargins(12, 12, 12, 12)
        slots_form.setSpacing(8)
        self._slot_gdl = QSpinBox()
        self._slot_ffmpeg = QSpinBox()
        self._slot_http = QSpinBox()
        for box in (self._slot_gdl, self._slot_ffmpeg, self._slot_http):
            box.setRange(1, 32)
        slots_form.addRow("網站擷取（gallery-dl）", self._slot_gdl)
        slots_form.addRow("影音處理（ffmpeg）", self._slot_ffmpeg)
        slots_form.addRow("網路請求（HTTP）", self._slot_http)
        root.addWidget(slots)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._processing_reload = QPushButton("重新載入")
        self._processing_save = QPushButton("儲存")
        buttons.addWidget(self._processing_reload)
        buttons.addWidget(self._processing_save)
        root.addLayout(buttons)
        root.addStretch(1)

    def _wire_actions(self) -> None:
        for tab in (self._queue_tab, self._history_tab):
            tab.refresh_button.clicked.connect(self.refresh)
            tab.status_filter.currentIndexChanged.connect(self.refresh)
            tab.type_filter.currentIndexChanged.connect(self.refresh)
            tab.source_filter.currentIndexChanged.connect(self.refresh)
            tab.table.customContextMenuRequested.connect(lambda pos, t=tab: self._open_context_menu(t, pos))
            tab.table.itemDoubleClicked.connect(lambda _item, t=tab: self._show_detail(t))

        self._processing_reload.clicked.connect(self._load_processing_settings)
        self._processing_save.clicked.connect(self._save_processing_settings)

    def _refresh_job_tab(self, tab: _JobTab) -> None:
        try:
            rows = self._controller.list_background_jobs(statuses=tab.statuses, limit=500)
            live = self._controller.live_job_snapshots()
        except Exception as exc:
            LOGGER.exception("background job refresh failed: %s", exc)
            return

        selected_job_id = self._selected_job_id(tab)
        status_filter = tab.status_filter.currentData()
        type_filter = tab.type_filter.currentData()
        source_filter = tab.source_filter.currentData()

        filtered: list[Any] = []
        for row in rows:
            if status_filter and row["status"] != status_filter:
                continue
            if type_filter and row["job_type"] != type_filter:
                continue
            if source_filter and row["source"] != source_filter:
                continue
            filtered.append(row)

        tab.table.setRowCount(0)
        selected_row = -1
        for index, row in enumerate(filtered):
            live_snapshot = live.get(int(row["id"]))
            progress = _progress_label(row, live_snapshot)
            r = tab.table.rowCount()
            tab.table.insertRow(r)
            values = [
                str(row["id"]),
                _status_label(str(row["status"])),
                _job_type_label(str(row["job_type"])),
                str(row["title"]),
                _source_label(str(row["source"])),
                progress,
                _fmt_ts(row["created_at"]),
                _fmt_ts(row["started_at"]),
                _fmt_ts(row["finished_at"]),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
                if col != 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                tab.table.setItem(r, col, item)
            if int(row["id"]) == selected_job_id:
                selected_row = index

        if selected_row >= 0:
            tab.table.selectRow(selected_row)

        running = sum(1 for row in filtered if str(row["status"]) == "running")
        queued = sum(1 for row in filtered if str(row["status"]) == "queued")
        parts = [f"目前顯示 {len(filtered)} 筆任務"]
        if queued:
            parts.append(f"等待中 {queued} 筆")
        if running:
            parts.append(f"執行中 {running} 筆")
        tab.summary.setText("，".join(parts))

    def _selected_job_id(self, tab: _JobTab) -> int | None:
        row = tab.table.currentRow()
        if row < 0:
            return None
        item = tab.table.item(row, 0)
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return None if value is None else int(value)

    def _open_context_menu(self, tab: _JobTab, pos) -> None:
        row_index = tab.table.rowAt(pos.y())
        if row_index < 0:
            return
        tab.table.selectRow(row_index)
        job_id = self._selected_job_id(tab)
        if job_id is None:
            return

        row = self._controller.fetch_background_job(job_id)
        if row is None:
            return
        status = str(row["status"])

        menu = QMenu(self)
        detail_action = menu.addAction("查看詳細資料")
        detail_action.triggered.connect(lambda: self._show_detail(tab))

        if status in ("queued", "running"):
            menu.addSeparator()
        if status == "queued":
            cancel_action = menu.addAction("取消任務")
            cancel_action.triggered.connect(lambda: self._cancel_selected(tab))
        if status == "running":
            cancel_action = menu.addAction("請求取消")
            cancel_action.triggered.connect(lambda: self._cancel_selected(tab))
            terminate_action = menu.addAction("強制終止")
            terminate_action.triggered.connect(lambda: self._terminate_selected(tab))
        if status in ("failed", "cancelled", "succeeded"):
            menu.addSeparator()
            retry_action = menu.addAction("重新排入佇列")
            retry_action.triggered.connect(lambda: self._retry_selected(tab))
            purge_action = menu.addAction("清除紀錄")
            purge_action.triggered.connect(lambda: self._purge_selected(tab))

        menu.exec(tab.table.viewport().mapToGlobal(pos))

    def _show_detail(self, tab: _JobTab) -> None:
        job_id = self._selected_job_id(tab)
        if job_id is None:
            return
        row = self._controller.fetch_background_job(job_id)
        if row is None:
            return

        events = self._controller.list_background_job_events(job_id)
        payload = json_loads(row["payload_json"], {})
        result = json_loads(row["result_json"], None)
        lines = [
            f"ID：{row['id']}",
            f"類型：{_job_type_label(str(row['job_type']))}",
            f"狀態：{_status_label(str(row['status']))}",
            f"來源：{_source_label(str(row['source']))}",
            f"標題：{row['title']}",
            f"建立時間：{_fmt_ts(row['created_at'])}",
            f"開始時間：{_fmt_ts(row['started_at'])}",
            f"結束時間：{_fmt_ts(row['finished_at'])}",
            f"嘗試次數：{row['attempt_count']} / {row['max_attempts']}",
            f"結果代碼：{row['outcome_code'] or '-'}",
            f"請求內容：{payload}",
        ]
        if result is not None:
            lines.append(f"執行結果：{result}")
        if row["error_text"]:
            lines.append(f"錯誤訊息：{row['error_text']}")
        if events:
            lines.append("")
            lines.append("事件紀錄：")
            for event in events:
                payload_text = json_loads(event["payload_json"], None)
                label = EVENT_TYPE_LABELS.get(str(event["event_type"]), str(event["event_type"]))
                suffix = "" if payload_text in (None, "", {}) else f" {payload_text}"
                lines.append(f"  [{event['created_at']}] {label}{suffix}")
        QMessageBox.information(self, f"任務 #{job_id}", "\n".join(lines))

    def _cancel_selected(self, tab: _JobTab) -> None:
        job_id = self._selected_job_id(tab)
        if job_id is None:
            return
        if self._controller.cancel_background_job(job_id):
            self.refresh()

    def _terminate_selected(self, tab: _JobTab) -> None:
        job_id = self._selected_job_id(tab)
        if job_id is None:
            return
        if self._controller.terminate_background_job(job_id):
            self.refresh()

    def _retry_selected(self, tab: _JobTab) -> None:
        job_id = self._selected_job_id(tab)
        if job_id is None:
            return
        if self._controller.retry_background_job(job_id):
            self.refresh()

    def _purge_selected(self, tab: _JobTab) -> None:
        job_id = self._selected_job_id(tab)
        if job_id is None:
            return
        if self._controller.purge_background_jobs(job_ids=[job_id]):
            self.refresh()

    def _load_processing_settings(self) -> None:
        self._run_idle.setChecked(bool(self._controller.get_setting("system", "background_work_during_idle", True)))
        self._run_active.setChecked(bool(self._controller.get_setting("system", "background_work_during_active", True)))
        self._idle_threshold.setValue(int(self._controller.get_setting("system", "background_idle_threshold_seconds", 20) or 20))
        self._poll_interval.setValue(int(self._controller.get_setting("system", "background_queue_poll_interval_ms", 1000) or 1000))
        self._global_concurrency.setValue(int(self._controller.get_setting("system", "background_queue_global_concurrency", 2) or 2))
        self._active_packet.setValue(int(self._controller.get_setting("system", "background_active_packet_ms", 1000) or 1000))
        self._active_rest.setValue(int(self._controller.get_setting("system", "background_active_rest_percentage", 200) or 200))
        self._idle_packet.setValue(int(self._controller.get_setting("system", "background_idle_packet_ms", 1000) or 1000))
        self._idle_rest.setValue(int(self._controller.get_setting("system", "background_idle_rest_percentage", 25) or 25))
        slot_limits = self._controller.get_setting("system", "background_slot_limits", {"gdl": 1, "ffmpeg": 1, "http": 2}) or {}
        self._slot_gdl.setValue(int(slot_limits.get("gdl", 1) or 1))
        self._slot_ffmpeg.setValue(int(slot_limits.get("ffmpeg", 1) or 1))
        self._slot_http.setValue(int(slot_limits.get("http", 2) or 2))

    def _save_processing_settings(self) -> None:
        self._controller.set_setting("system", "background_work_during_idle", self._run_idle.isChecked())
        self._controller.set_setting("system", "background_work_during_active", self._run_active.isChecked())
        self._controller.set_setting("system", "background_idle_threshold_seconds", int(self._idle_threshold.value()))
        self._controller.set_setting("system", "background_queue_poll_interval_ms", int(self._poll_interval.value()))
        self._controller.set_setting("system", "background_queue_global_concurrency", int(self._global_concurrency.value()))
        self._controller.set_setting("system", "background_active_packet_ms", int(self._active_packet.value()))
        self._controller.set_setting("system", "background_active_rest_percentage", int(self._active_rest.value()))
        self._controller.set_setting("system", "background_idle_packet_ms", int(self._idle_packet.value()))
        self._controller.set_setting("system", "background_idle_rest_percentage", int(self._idle_rest.value()))
        self._controller.set_setting(
            "system",
            "background_slot_limits",
            {
                "gdl": int(self._slot_gdl.value()),
                "ffmpeg": int(self._slot_ffmpeg.value()),
                "http": int(self._slot_http.value()),
            },
        )
        self._controller.wake_background_processing()

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()
