"""Overview tab — single creator's snapshot view (spec §9).

Layout:

- 摘要區 (ReflowGrid: info card + stat group; folds to single column on narrow)
- 最近活動區 (ReflowGrid: 最近貼文 / 最近工作紀錄; folds to single column)
- Stat group itself uses a ReflowGrid (3 / 2 / 1 columns based on width)

Caller wires up :py:meth:`set_navigator` so stat cards and recent
worklog entries can open / focus the relevant maintenance tab.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.config import LOGGER
from core.controller import Controller
from ..widgets import ReflowGrid

OverviewNavigator = Callable[..., None]


def _short(text: str | None, length: int) -> str:
    if not text:
        return ""
    text = str(text)
    return text if len(text) <= length else text[: max(0, length - 1)] + "…"


class _StatCard(QFrame):
    def __init__(
        self,
        label: str,
        *,
        target_kind: str,
        on_click: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._target_kind = target_kind
        self._on_click = on_click
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("OverviewStatCard")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        self._value = QLabel("—", self)
        self._value.setObjectName("OverviewStatValue")
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value.setStyleSheet("font-size: 18pt; font-weight: bold;")
        self._label = QLabel(label, self)
        self._label.setObjectName("OverviewStatLabel")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._value)
        layout.addWidget(self._label)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value: str) -> None:
        self._value.setText(value)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._on_click is not None:
            self._on_click(self._target_kind)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class OverviewTab(QWidget):
    """Per-creator overview view."""

    def __init__(
        self,
        controller: Controller,
        creator_id: int,
        *,
        navigator: OverviewNavigator | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._creator_id = int(creator_id)
        self._navigator = navigator

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ── 摘要區 ─────────────────────────────────────────────────────────
        summary_group = QGroupBox("摘要")
        summary_outer = QVBoxLayout(summary_group)
        summary_outer.setContentsMargins(10, 10, 10, 10)

        # 2-column at >= 800 px (info | stats), single column below.
        summary_grid = ReflowGrid([(800, 2)])
        summary_outer.addWidget(summary_grid)

        # 左：基本資訊卡
        info_frame = QFrame()
        info_frame.setFrameShape(QFrame.Shape.StyledPanel)
        info_grid = QGridLayout(info_frame)
        info_grid.setContentsMargins(10, 8, 10, 8)
        info_grid.setHorizontalSpacing(12)
        info_grid.setVerticalSpacing(4)
        self._name_label = QLabel("—")
        self._name_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        self._name_label.setWordWrap(True)
        self._id_label = QLabel("—")
        self._status_label = QLabel("—")
        self._updated_label = QLabel("—")
        self._note_label = QLabel("")
        self._note_label.setWordWrap(True)
        info_grid.addWidget(QLabel("主名稱"), 0, 0, Qt.AlignmentFlag.AlignTop)
        info_grid.addWidget(self._name_label, 0, 1)
        info_grid.addWidget(QLabel("creator_id"), 1, 0)
        info_grid.addWidget(self._id_label, 1, 1)
        info_grid.addWidget(QLabel("狀態"), 2, 0)
        info_grid.addWidget(self._status_label, 2, 1)
        info_grid.addWidget(QLabel("最後更新"), 3, 0)
        info_grid.addWidget(self._updated_label, 3, 1)
        info_grid.addWidget(QLabel("備註"), 4, 0, Qt.AlignmentFlag.AlignTop)
        info_grid.addWidget(self._note_label, 4, 1)
        info_grid.setColumnStretch(1, 1)
        summary_grid.add_widget(info_frame)

        # 右：統計卡（自身也是 ReflowGrid）
        stats_grid = ReflowGrid([(640, 3), (400, 2)])
        self._stat_profile = _StatCard("profile", target_kind="URLs", on_click=self._open_kind)
        self._stat_alias = _StatCard("alias", target_kind="Aliases", on_click=self._open_kind)
        self._stat_url = _StatCard("URL", target_kind="URLs", on_click=self._open_kind)
        self._stat_post = _StatCard("post", target_kind="Posts", on_click=self._open_kind)
        self._stat_work = _StatCard("worklog", target_kind="Worklogs", on_click=self._open_kind)
        self._stat_reminder = _StatCard("reminder", target_kind="Reminder", on_click=self._open_kind)
        self._stat_profile.setToolTip("開啟 URLs 維護頁")
        self._stat_alias.setToolTip("開啟 Aliases 維護頁")
        self._stat_url.setToolTip("開啟 URLs 維護頁")
        self._stat_post.setToolTip("開啟 Posts 維護頁")
        self._stat_work.setToolTip("開啟 Worklogs 維護頁")
        self._stat_reminder.setToolTip("開啟 Reminder 維護頁")
        for card in (
            self._stat_profile,
            self._stat_alias,
            self._stat_url,
            self._stat_post,
            self._stat_work,
            self._stat_reminder,
        ):
            stats_grid.add_widget(card)
        summary_grid.add_widget(stats_grid)

        root.addWidget(summary_group)

        # ── 最近活動區 ─────────────────────────────────────────────────────
        recent_group = QGroupBox("最近活動")
        recent_outer = QVBoxLayout(recent_group)
        recent_outer.setContentsMargins(10, 10, 10, 10)

        recent_grid = ReflowGrid([(720, 2)])

        posts_box = QWidget()
        posts_layout = QVBoxLayout(posts_box)
        posts_layout.setContentsMargins(0, 0, 0, 0)
        posts_layout.addWidget(QLabel("最近貼文"))
        self._posts_list = QListWidget()
        self._posts_list.setAlternatingRowColors(True)
        posts_layout.addWidget(self._posts_list, 1)
        recent_grid.add_widget(posts_box, stretch=1)

        works_box = QWidget()
        works_layout = QVBoxLayout(works_box)
        works_layout.setContentsMargins(0, 0, 0, 0)
        works_layout.addWidget(QLabel("最近工作紀錄"))
        self._works_list = QListWidget()
        self._works_list.setAlternatingRowColors(True)
        self._works_list.itemClicked.connect(self._on_work_activated)
        self._works_list.itemActivated.connect(self._on_work_activated)
        works_layout.addWidget(self._works_list, 1)
        recent_grid.add_widget(works_box, stretch=1)

        recent_outer.addWidget(recent_grid)
        root.addWidget(recent_group, 1)

        self.refresh()

    # ── public API ─────────────────────────────────────────────────────────
    @property
    def creator_id(self) -> int:
        return self._creator_id

    def set_navigator(self, navigator: OverviewNavigator) -> None:
        self._navigator = navigator

    def refresh(self) -> None:
        try:
            snapshot = self._controller.read("creator_snapshot", self._creator_id)
        except Exception as exc:
            LOGGER.exception("Overview refresh failed for #%s: %s", self._creator_id, exc)
            self._name_label.setText(f"(無法讀取 #{self._creator_id})")
            return
        self._render(snapshot)

    # ── render ─────────────────────────────────────────────────────────────
    def _render(self, snapshot: dict[str, Any]) -> None:
        creator = snapshot["creator"]
        self._name_label.setText(str(creator["primary_name"]))
        self._id_label.setText(f"#{creator['id']}")
        self._status_label.setText(str(creator["status"] or ""))
        self._updated_label.setText(str(creator["updated_at"] or ""))
        self._note_label.setText(str(creator["note"] or ""))

        profiles = snapshot["profiles"] or []
        aliases = snapshot["aliases"] or []
        posts = snapshot["posts"] or []
        work = snapshot["work"] or []
        reminder = snapshot["reminder"]

        url_count = sum(len(p.get("urls") or []) for p in profiles)
        self._stat_profile.set_value(str(len(profiles)))
        self._stat_alias.set_value(str(len(aliases)))
        self._stat_url.set_value(str(url_count))
        self._stat_post.set_value(str(len(posts)))
        self._stat_work.set_value(str(len(work)))
        if reminder is None:
            self._stat_reminder.set_value("—")
        else:
            due = str(reminder["next_due_at"])[:10] if reminder["next_due_at"] else "—"
            self._stat_reminder.set_value(due)

        # Recent posts (cap at 5 per spec §9.4)
        self._posts_list.clear()
        for row in posts[:5]:
            title = row["title"] or row["url"] or ""
            display_name = row["display_name"] or row["platform"] or ""
            captured = str(row["captured_at"] or "")[:10]
            posted = str(row["posted_at"] or "")[:10] if row["posted_at"] else ""
            second_line = " · ".join(
                p
                for p in [display_name, captured and f"擷取 {captured}", posted and f"上傳 {posted}"]
                if p
            )
            text = f"{_short(title, 80)}"
            if second_line:
                text += f"\n    {second_line}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            item.setToolTip(text)
            self._posts_list.addItem(item)

        # Recent worklogs (cap at 5)
        self._works_list.clear()
        for row in work[:5]:
            content = row["content"] or ""
            first_line = content.splitlines()[0] if content else ""
            created = str(row["created_at"] or "")[:16]
            text = f"{_short(first_line, 80)}"
            if created:
                text += f"\n    {created}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            item.setToolTip(text)
            self._works_list.addItem(item)

    # ── interactions ───────────────────────────────────────────────────────
    def _open_kind(self, kind: str) -> None:
        if self._navigator is None:
            return
        try:
            self._navigator(kind, self._creator_id)
        except Exception as exc:
            LOGGER.exception("overview navigation failed: %s", exc)

    def _on_work_activated(self, item: QListWidgetItem) -> None:
        worklog_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(worklog_id, int) or self._navigator is None:
            return
        try:
            self._navigator("Worklogs", self._creator_id, focus_id=worklog_id)
        except Exception as exc:
            LOGGER.exception("worklog navigation failed: %s", exc)
