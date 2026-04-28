from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import LOGGER, log_path, log_exception
from .constants import AmbiguousTarget, UserError
from .db import (
    candidate_lines,
    connect,
    creator_label,
    due_rows,
    fetch_creator_snapshot,
    fetch_post_detail_row,
    fetch_work_detail_row,
    grouped_search,
    recent_creators,
    recent_posts,
    recent_worklogs,
)
from .render import format_kind_counts, render_creator_snapshot, render_post_detail, render_work_detail
from .service import (
    add_creator_record,
    add_name_record,
    add_post_record,
    add_url_record,
    add_work_record,
    set_reminder_record,
)
from .utils import json_loads, shorten, split_values

_TEXTUAL_IMPORT_ERROR: Exception | None = None

try:
    from textual import events, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical, VerticalScroll
    from textual.coordinate import Coordinate
    from textual.screen import ModalScreen
    from textual.worker import Worker, WorkerState
    from textual.widgets import (
        Button, DataTable, Footer, Header, Input,
        Static, TabbedContent, TabPane, TextArea,
    )
except Exception as _exc:
    App = None  # type: ignore[assignment]
    ComposeResult = Any  # type: ignore[assignment]
    Binding = Any  # type: ignore[assignment]
    events = None  # type: ignore[assignment]
    Coordinate = None  # type: ignore[assignment]
    work = Any  # type: ignore[assignment]
    Worker = WorkerState = Any  # type: ignore[assignment]
    Container = Horizontal = Vertical = VerticalScroll = ModalScreen = object  # type: ignore[assignment]
    Button = DataTable = Footer = Header = Input = Static = TabbedContent = TabPane = TextArea = object  # type: ignore[assignment]
    _TEXTUAL_IMPORT_ERROR = _exc


def ensure_textual() -> None:
    if App is None or _TEXTUAL_IMPORT_ERROR is not None:
        raise UserError(
            f"Interactive mode requires Textual. Install it or use a CLI command. {_TEXTUAL_IMPORT_ERROR or ''}".strip()
        )


if App is not None:
    class RecordFormScreen(ModalScreen[dict[str, str] | None]):
        CSS = """
        RecordFormScreen {
            align: center middle;
            background: rgba(3, 7, 18, 0.72);
        }

        #dialog {
            width: 84;
            max-width: 120;
            height: auto;
            max-height: 90%;
            padding: 1 2;
            background: #101826;
            border: round #3c7cff;
        }

        .dialog-title {
            text-style: bold;
            color: #f8fafc;
            padding-bottom: 1;
        }

        .dialog-field-label {
            color: #93c5fd;
            padding-top: 1;
        }

        #dialog-fields {
            max-height: 20;
        }

        RecordFormScreen TextArea {
            height: 5;
            min-height: 3;
        }

        .dialog-buttons {
            height: auto;
            align-horizontal: right;
            padding-top: 1;
        }

        .dialog-buttons Button {
            margin-left: 1;
        }
        """

        def __init__(
            self,
            title: str,
            fields: list[dict[str, Any]],
            *,
            submit_label: str = "Save",
        ) -> None:
            super().__init__()
            self.title = title
            self.fields = fields
            self.submit_label = submit_label

        def compose(self) -> ComposeResult:
            LOGGER.info("TUI form compose: %s fields=%s", self.title, len(self.fields))
            with Container(id="dialog"):
                yield Static(self.title, classes="dialog-title")
                with VerticalScroll(id="dialog-fields"):
                    for field in self.fields:
                        yield Static(field["label"], classes="dialog-field-label")
                        field_id = f"field-{field['name']}"
                        if field.get("kind") == "textarea":
                            yield TextArea(
                                text=str(field.get("value", "")),
                                placeholder=str(field.get("placeholder", "")),
                                id=field_id,
                            )
                        else:
                            yield Input(
                                value=str(field.get("value", "")),
                                placeholder=str(field.get("placeholder", "")),
                                id=field_id,
                            )
                with Horizontal(classes="dialog-buttons"):
                    yield Button("Cancel", id="cancel")
                    yield Button(self.submit_label, id="submit", variant="primary")

        def on_mount(self) -> None:
            LOGGER.info("TUI form mount: %s", self.title)
            if not self.fields:
                return
            self.call_after_refresh(self.focus_first_field)

        def focus_first_field(self) -> None:
            if not self.fields:
                return
            first_id = f"#field-{self.fields[0]['name']}"
            LOGGER.info("TUI form focus first: %s target=%s", self.title, first_id)
            try:
                self.query_one(first_id).focus()
            except Exception as exc:
                log_exception(f"TUI form focus failed {self.title}", exc)
            else:
                LOGGER.info("TUI form focus ready: %s", self.title)

        def field_value(self, name: str) -> str:
            widget = self.query_one(f"#field-{name}")
            if isinstance(widget, TextArea):
                return widget.text
            return widget.value

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "cancel":
                LOGGER.info("TUI form cancel: %s", self.title)
                self.dismiss(None)
                return
            data = {field["name"]: self.field_value(field["name"]).strip() for field in self.fields}
            LOGGER.info("TUI form submit: %s keys=%s", self.title, ",".join(sorted(data)))
            self.dismiss(data)

    class DetailScreen(ModalScreen[None]):
        CSS = """
        DetailScreen {
            align: center middle;
            background: rgba(3, 7, 18, 0.72);
        }

        #detail-modal {
            width: 90%;
            max-width: 110;
            height: 90%;
            padding: 1 2;
            background: #101826;
            border: round #3c7cff;
        }

        #detail-modal-title {
            height: auto;
            color: #f8fafc;
            text-style: bold;
            padding-bottom: 1;
        }

        #detail-modal-body {
            height: 1fr;
            background: #111b2d;
            border: round #35557f;
        }

        #detail-modal-content {
            padding: 1;
        }

        #detail-modal-buttons {
            height: auto;
            align-horizontal: right;
            padding-top: 1;
        }
        """

        BINDINGS = [Binding("escape", "dismiss", "Close", show=True)]

        def __init__(self, title: str, body: Any) -> None:
            super().__init__()
            self.detail_title = title
            self.detail_body = body

        def compose(self) -> ComposeResult:
            with Container(id="detail-modal"):
                yield Static(self.detail_title, id="detail-modal-title")
                with VerticalScroll(id="detail-modal-body"):
                    yield Static(self.detail_body, id="detail-modal-content")
                with Horizontal(id="detail-modal-buttons"):
                    yield Button("Close", id="close", variant="primary")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "close":
                self.dismiss(None)

    class ContextMenuScreen(ModalScreen[str | None]):
        CSS = """
        ContextMenuScreen {
            align: center middle;
            background: rgba(3, 7, 18, 0.72);
        }

        #ctx-menu {
            width: 36;
            height: auto;
            padding: 1 2;
            background: #101826;
            border: round #3c7cff;
        }

        #ctx-menu-title {
            text-style: bold;
            color: #f8fafc;
            padding-bottom: 1;
        }

        #ctx-menu Button {
            width: 1fr;
            margin-top: 1;
        }
        """

        BINDINGS = [Binding("escape", "dismiss", "Close", show=True)]

        def __init__(self, header: str) -> None:
            super().__init__()
            self.header = header

        def compose(self) -> ComposeResult:
            with Container(id="ctx-menu"):
                yield Static(self.header, id="ctx-menu-title")
                yield Button("View Detail", id="ctx-view")
                yield Button("Add Name", id="ctx-name")
                yield Button("Add URL", id="ctx-url")
                yield Button("Record Post", id="ctx-post")
                yield Button("Set Reminder", id="ctx-remind")
                yield Button("Add Work", id="ctx-work")
                yield Button("Cancel", id="ctx-cancel")

        def on_mount(self) -> None:
            try:
                self.query_one("#ctx-view", Button).focus()
            except Exception:
                pass

        def on_button_pressed(self, event: Button.Pressed) -> None:
            bid = event.button.id or ""
            if bid == "ctx-cancel":
                self.dismiss(None)
                return
            mapping = {
                "ctx-view": "view",
                "ctx-name": "name",
                "ctx-url": "url",
                "ctx-post": "post",
                "ctx-remind": "remind",
                "ctx-work": "work",
            }
            self.dismiss(mapping.get(bid))

    class ClogApp(App[None]):
        TITLE = "CreatorLog"
        SUB_TITLE = "Interactive Mode"
        CSS = """
        Screen {
            background: #0b1220;
            color: #e5eef8;
        }

        #chrome {
            layout: vertical;
            height: 1fr;
        }

        #search-row {
            height: auto;
            padding: 1 1 0 1;
            background: #0f172a;
            border-bottom: solid #22324a;
        }

        #search-input {
            width: 1fr;
        }

        #body {
            layout: horizontal;
            height: 1fr;
        }

        #sidebar {
            width: 30;
            min-width: 24;
            padding: 1;
            background: #0f172a;
            border-right: solid #22324a;
        }

        #main {
            width: 1fr;
            min-width: 20;
            padding: 1;
        }

        #detail {
            width: 40;
            min-width: 30;
            padding: 1;
            background: #0f172a;
            border-left: solid #22324a;
        }

        .pane-title {
            height: auto;
            color: #7dd3fc;
            text-style: bold;
            padding: 0 0 1 0;
        }

        .table-block {
            height: 1fr;
            margin-bottom: 1;
        }

        #sidebar-creators {
            height: 1fr;
        }

        #due-table {
            height: 1fr;
            max-height: 16;
        }

        #page-bar {
            height: auto;
            padding-bottom: 1;
            align-vertical: middle;
        }

        #page-label {
            width: 1fr;
            content-align: center middle;
            color: #93c5fd;
        }

        #detail-title {
            height: auto;
            color: #f8fafc;
            text-style: bold;
            padding-bottom: 1;
        }

        #detail-body {
            height: 1fr;
            background: #111b2d;
            border: round #35557f;
        }

        #detail-content {
            padding: 1;
        }

        TabbedContent {
            height: 1fr;
        }

        TabPane {
            padding: 0;
        }

        DataTable {
            height: 1fr;
            background: #0f172a;
        }

        Footer {
            background: #101826;
        }

        /* Responsive class rules toggled by on_resize */
        #chrome.narrow #sidebar { display: none; }
        #chrome.narrow #detail { display: none; }
        #chrome.narrow #prev-page { display: none; }
        #chrome.narrow #next-page { display: none; }
        #chrome.medium #detail { display: none; }
        """

        BINDINGS = [
            Binding("/", "focus_search", "Search", show=True),
            Binding("m", "open_menu", "Menu", show=True),
            Binding("ctrl+a", "add_creator", "Add", show=True),
            Binding("ctrl+n", "add_name", "Name", show=True),
            Binding("ctrl+u", "add_url", "URL", show=True),
            Binding("ctrl+p", "add_post", "Post", show=True),
            Binding("ctrl+r", "add_reminder", "Remind", show=True),
            Binding("ctrl+w", "add_work", "Work", show=True),
            Binding("[", "prev_page", "Prev", show=True),
            Binding("]", "next_page", "Next", show=True),
            Binding("f5", "refresh_data", "Refresh", show=True),
        ]

        def __init__(self, db_path: Path) -> None:
            super().__init__()
            self.db_path = db_path
            self.conn = connect(db_path)
            self.form_action_running = False
            self.search_query_text = ""
            self.page_size = 15
            self.pages = {"creators": 0, "posts": 0, "work": 0}
            self.has_next_page = {"creators": False, "posts": False, "work": False}
            self.table_keys: dict[str, list[int]] = {}
            self.current_detail_kind: str | None = None
            self.current_detail_id: int | None = None
            self.current_creator_id: int | None = None

        def close_connection(self) -> None:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as exc:
                LOGGER.warning("DB checkpoint failed: %s", exc)
            try:
                self.conn.close()
            except Exception as exc:
                LOGGER.warning("DB close failed: %s", exc)
            else:
                LOGGER.info("DB closed %s", self.db_path)

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Container(id="chrome"):
                with Horizontal(id="search-row"):
                    yield Input(placeholder="Search creators, urls, platform ids, posts, work... (m: row menu)", id="search-input")
                with Horizontal(id="body"):
                    with Vertical(id="sidebar"):
                        yield Static("Due Reminders", classes="pane-title")
                        yield DataTable(id="due-table", classes="table-block")
                        yield Static("Recent Creators", classes="pane-title")
                        yield DataTable(id="sidebar-creators", classes="table-block")
                    with Vertical(id="main"):
                        with Horizontal(id="page-bar"):
                            yield Button("< Prev", id="prev-page")
                            yield Static("", id="page-label")
                            yield Button("Next >", id="next-page")
                        with TabbedContent(initial="search-pane", id="main-tabs"):
                            with TabPane("Search", id="search-pane"):
                                yield DataTable(id="search-table")
                            with TabPane("Creators", id="creators-pane"):
                                yield DataTable(id="creators-table")
                            with TabPane("Posts", id="posts-pane"):
                                yield DataTable(id="posts-table")
                            with TabPane("Work", id="work-pane"):
                                yield DataTable(id="work-table")
                    with Vertical(id="detail"):
                        yield Static("No Selection", id="detail-title")
                        with VerticalScroll(id="detail-body"):
                            yield Static("Type in the search box or pick a recent item.", id="detail-content")
            yield Footer()

        def on_mount(self) -> None:
            LOGGER.info("TUI mount db=%s log=%s", self.db_path, log_path())
            self.configure_tables()
            self.apply_size_class(self.size.width)
            self.refresh_all()
            self.query_one("#search-input", Input).focus()

        def on_unmount(self) -> None:
            LOGGER.info("TUI unmount workers=%s", len(list(self.workers)))

        def apply_size_class(self, width: int) -> None:
            try:
                chrome = self.query_one("#chrome")
            except Exception:
                return
            chrome.set_class(width < 80, "narrow")
            chrome.set_class(80 <= width < 120, "medium")
            chrome.set_class(width >= 120, "wide")

        def on_resize(self, event: events.Resize) -> None:
            previous_wide = self.is_wide()
            self.apply_size_class(event.size.width)
            now_wide = self.is_wide()
            if now_wide and not previous_wide and self.current_detail_kind and self.current_detail_id:
                try:
                    self.set_detail(self.current_detail_kind, self.current_detail_id)
                except UserError:
                    pass

        def get_active_table(self) -> Any:
            focused = self.focused
            if isinstance(focused, DataTable):
                return focused
            try:
                active = self.query_one("#main-tabs", TabbedContent).active
            except Exception:
                return None
            pane_to_table = {
                "search-pane": "#search-table",
                "creators-pane": "#creators-table",
                "posts-pane": "#posts-table",
                "work-pane": "#work-table",
            }
            table_id = pane_to_table.get(active)
            if table_id is None:
                return None
            try:
                return self.query_one(table_id, DataTable)
            except Exception:
                return None

        def on_click(self, event: events.Click) -> None:
            if getattr(event, "button", 1) != 3:
                return
            if not isinstance(self.focused, DataTable):
                return
            self.action_open_menu()

        def action_open_menu(self) -> None:
            table = self.get_active_table()
            if table is None or table.row_count == 0 or table.cursor_row < 0:
                return
            try:
                cell_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0))
                row_key = cell_key.row_key
            except Exception:
                return
            entity_id = self.row_key_value(row_key)
            if entity_id is None:
                return
            table_id = table.id or ""
            kind = self.kind_for_table(table_id)
            if kind is None:
                return
            try:
                creator_id, _, _ = self.compute_detail(kind, entity_id)
            except UserError as exc:
                self.notify_user_error(exc)
                return
            self.current_creator_id = creator_id
            self.current_detail_kind = kind
            self.current_detail_id = entity_id
            try:
                header = creator_label(self.conn, creator_id)
            except Exception:
                header = f"#{creator_id}"

            def on_dismiss(action: str | None) -> None:
                if action is None:
                    return
                if action == "view":
                    self.show_detail(kind, entity_id)
                elif action == "name":
                    self.action_add_name()
                elif action == "url":
                    self.action_add_url()
                elif action == "post":
                    self.action_add_post()
                elif action == "remind":
                    self.action_add_reminder()
                elif action == "work":
                    self.action_add_work()

            self.push_screen(ContextMenuScreen(header), callback=on_dismiss)

        def configure_tables(self) -> None:
            for table_id, columns in {
                "#due-table": ["Due", "Creator"],
                "#sidebar-creators": ["Creator", "Updated"],
                "#search-table": ["ID", "Creator", "Matches", "Accounts", "URLs"],
                "#creators-table": ["ID", "Creator", "URLs", "Posts", "Work", "Updated"],
                "#posts-table": ["Post", "Creator", "Title", "Platform", "Captured"],
                "#work-table": ["Work", "Creator", "Message", "Tags", "Created"],
            }.items():
                table = self.query_one(table_id, DataTable)
                table.cursor_type = "row"
                table.zebra_stripes = True
                for column in columns:
                    table.add_column(column)

        def clear_table(self, table_id: str) -> DataTable:
            table = self.query_one(table_id, DataTable)
            table.clear(columns=False)
            return table

        def row_key_value(self, row_key: Any) -> int | None:
            if row_key is None:
                return None
            value = getattr(row_key, "value", row_key)
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        def current_target(self) -> str:
            return f"#{self.current_creator_id}" if self.current_creator_id else ""

        def active_recent_kind(self) -> str | None:
            active = self.query_one("#main-tabs", TabbedContent).active
            mapping = {
                "creators-pane": "creators",
                "posts-pane": "posts",
                "work-pane": "work",
            }
            return mapping.get(active)

        def is_wide(self) -> bool:
            try:
                return self.query_one("#chrome").has_class("wide")
            except Exception:
                return False

        def compute_detail(self, kind: str, entity_id: int) -> tuple[int, str, Any]:
            if kind == "creator":
                snapshot = fetch_creator_snapshot(self.conn, entity_id)
                creator = snapshot["creator"]
                return (
                    int(creator["id"]),
                    f"Creator #{creator['id']} {creator['primary_name']}",
                    render_creator_snapshot(snapshot),
                )
            if kind == "post":
                row = fetch_post_detail_row(self.conn, entity_id)
                return (
                    int(row["creator_id"]),
                    f"Post {row['id']} -> #{row['creator_id']} {row['primary_name']}",
                    render_post_detail(row),
                )
            if kind == "work":
                row = fetch_work_detail_row(self.conn, entity_id)
                return (
                    int(row["creator_id"]),
                    f"Work {row['id']} -> #{row['creator_id']} {row['primary_name']}",
                    render_work_detail(row),
                )
            raise UserError(f"Unknown detail kind: {kind}")

        def write_side_detail(self, title_str: str, body: Any) -> None:
            try:
                self.query_one("#detail-title", Static).update(title_str)
                self.query_one("#detail-content", Static).update(body)
            except Exception:
                pass

        def set_detail(self, kind: str, entity_id: int) -> None:
            try:
                creator_id, title_str, body = self.compute_detail(kind, entity_id)
            except UserError:
                return
            self.current_creator_id = creator_id
            self.current_detail_kind = kind
            self.current_detail_id = entity_id
            if self.is_wide():
                self.write_side_detail(title_str, body)

        def show_detail(self, kind: str, entity_id: int) -> None:
            try:
                creator_id, title_str, body = self.compute_detail(kind, entity_id)
            except UserError as exc:
                self.notify_user_error(exc)
                return
            self.current_creator_id = creator_id
            self.current_detail_kind = kind
            self.current_detail_id = entity_id
            if self.is_wide():
                self.write_side_detail(title_str, body)
            else:
                self.push_screen(DetailScreen(title_str, body))

        def ensure_default_detail(self) -> None:
            if self.current_detail_kind == "creator" and self.current_detail_id:
                self.set_detail("creator", self.current_detail_id)
                return
            if self.current_detail_kind == "post" and self.current_detail_id:
                self.set_detail("post", self.current_detail_id)
                return
            if self.current_detail_kind == "work" and self.current_detail_id:
                self.set_detail("work", self.current_detail_id)
                return
            recent = recent_creators(self.conn, 1)
            if recent:
                self.set_detail("creator", int(recent[0]["id"]))

        def refresh_sidebar(self) -> None:
            due_table = self.clear_table("#due-table")
            self.table_keys["due-table"] = []
            for row in due_rows(self.conn)[:8]:
                due_table.add_row(row["next_due_at"], row["primary_name"], key=str(row["creator_id"]))
                self.table_keys["due-table"].append(int(row["creator_id"]))
            creators_table = self.clear_table("#sidebar-creators")
            self.table_keys["sidebar-creators"] = []
            for row in recent_creators(self.conn, 8):
                creators_table.add_row(
                    f"#{row['id']} {row['primary_name']}",
                    shorten(row["updated_at"], 19),
                    key=str(row["id"]),
                )
                self.table_keys["sidebar-creators"].append(int(row["id"]))

        def refresh_search_table(self) -> None:
            table = self.clear_table("#search-table")
            self.table_keys["search-table"] = []
            query = self.search_query_text.strip()
            if not query:
                for row in recent_creators(self.conn, 20):
                    meta = f"recent | urls {row['url_count']} posts {row['post_count']} work {row['work_count']}"
                    table.add_row(
                        f"#{row['id']}",
                        row["primary_name"],
                        meta,
                        "-",
                        shorten(row["updated_at"], 19),
                        key=str(row["id"]),
                    )
                    self.table_keys["search-table"].append(int(row["id"]))
                return
            for group in grouped_search(self.conn, query, limit=20):
                creator = group["creator"]
                accounts = ", ".join(group["accounts"][:2]) or "-"
                urls = "; ".join(shorten(url, 32) for _, url in group["urls"][:2]) or "-"
                matches = format_kind_counts(group["kind_counts"])
                table.add_row(
                    f"#{creator['id']}",
                    creator["primary_name"],
                    matches or "-",
                    accounts,
                    urls,
                    key=str(creator["id"]),
                )
                self.table_keys["search-table"].append(int(creator["id"]))

        def refresh_recent_table(self, kind: str) -> None:
            offset = self.pages[kind] * self.page_size
            limit = self.page_size + 1
            if kind == "creators":
                rows = recent_creators(self.conn, limit, offset)
                table = self.clear_table("#creators-table")
                self.table_keys["creators-table"] = []
                page_rows = rows[: self.page_size]
                self.has_next_page[kind] = len(rows) > self.page_size
                for row in page_rows:
                    table.add_row(
                        f"#{row['id']}",
                        row["primary_name"],
                        str(row["url_count"]),
                        str(row["post_count"]),
                        str(row["work_count"]),
                        shorten(row["updated_at"], 19),
                        key=str(row["id"]),
                    )
                    self.table_keys["creators-table"].append(int(row["id"]))
                return
            if kind == "posts":
                rows = recent_posts(self.conn, limit, offset)
                table = self.clear_table("#posts-table")
                self.table_keys["posts-table"] = []
                page_rows = rows[: self.page_size]
                self.has_next_page[kind] = len(rows) > self.page_size
                for row in page_rows:
                    table.add_row(
                        f"post:{row['id']}",
                        f"#{row['creator_id']} {row['primary_name']}",
                        shorten(row["title"] or row["url"], 50),
                        row["platform"] or "-",
                        shorten(row["captured_at"], 19),
                        key=str(row["id"]),
                    )
                    self.table_keys["posts-table"].append(int(row["id"]))
                return
            rows = recent_worklogs(self.conn, limit, offset)
            table = self.clear_table("#work-table")
            self.table_keys["work-table"] = []
            page_rows = rows[: self.page_size]
            self.has_next_page[kind] = len(rows) > self.page_size
            for row in page_rows:
                tags = ",".join(json_loads(row["tags_json"], [])[:3]) or "-"
                table.add_row(
                    f"work:{row['id']}",
                    f"#{row['creator_id']} {row['primary_name']}",
                    shorten(row["message"], 44),
                    tags,
                    shorten(row["created_at"], 19),
                    key=str(row["id"]),
                )
                self.table_keys["work-table"].append(int(row["id"]))

        def refresh_page_bar(self) -> None:
            kind = self.active_recent_kind()
            prev_button = self.query_one("#prev-page", Button)
            next_button = self.query_one("#next-page", Button)
            label = self.query_one("#page-label", Static)
            if not kind:
                prev_button.disabled = True
                next_button.disabled = True
                query = self.search_query_text.strip()
                label.update(f"Search {'results' if query else 'home'}")
                return
            prev_button.disabled = self.pages[kind] <= 0
            next_button.disabled = not self.has_next_page[kind]
            label.update(f"{kind.title()} page {self.pages[kind] + 1}")

        def refresh_all(self) -> None:
            self.refresh_sidebar()
            self.refresh_search_table()
            self.refresh_recent_table("creators")
            self.refresh_recent_table("posts")
            self.refresh_recent_table("work")
            self.refresh_page_bar()
            self.ensure_default_detail()

        def status(self, message: str) -> None:
            self.sub_title = message

        def notify_user_error(self, exc: Exception) -> None:
            LOGGER.warning("TUI user error: %s", exc)
            if isinstance(exc, AmbiguousTarget):
                candidates = candidate_lines(self.conn, exc.creator_ids)
                suffix = f" Candidates: {'; '.join(line.strip() for line in candidates[:3])}" if candidates else ""
                self.notify(f"Ambiguous target: {exc.target}.{suffix}", severity="error", timeout=8)
                return
            self.notify(str(exc), severity="error", timeout=8)

        def notify_internal_error(self, action_name: str, exc: Exception) -> None:
            log_exception(f"TUI {action_name}", exc)
            LOGGER.error("TUI action failed: %s see=%s", action_name, log_path().name)

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id != "search-input":
                return
            self.search_query_text = event.value
            self.query_one("#main-tabs", TabbedContent).active = "search-pane"
            self.refresh_search_table()
            self.refresh_page_bar()
            first_key = self.table_keys.get("search-table", [])
            if first_key:
                try:
                    self.set_detail("creator", first_key[0])
                except UserError as exc:
                    self.notify_user_error(exc)

        def kind_for_table(self, table_id: str) -> str | None:
            if table_id in {"due-table", "sidebar-creators", "search-table", "creators-table"}:
                return "creator"
            if table_id == "posts-table":
                return "post"
            if table_id == "work-table":
                return "work"
            return None

        def handle_table_pick(self, table_id: str, row_key: Any, *, force_modal: bool = False) -> None:
            entity_id = self.row_key_value(row_key)
            if entity_id is None:
                return
            kind = self.kind_for_table(table_id)
            if kind is None:
                return
            try:
                if force_modal:
                    self.show_detail(kind, entity_id)
                else:
                    self.set_detail(kind, entity_id)
            except UserError as exc:
                self.notify_user_error(exc)

        def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
            self.handle_table_pick(event.data_table.id or "", event.row_key, force_modal=True)

        def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
            self.handle_table_pick(event.data_table.id or "", event.row_key, force_modal=False)

        def on_tabbed_content_tab_activated(self, _event: TabbedContent.TabActivated) -> None:
            self.refresh_page_bar()

        def start_action_worker(self, action_name: str, action: Callable[[], Awaitable[None]]) -> None:
            if self.form_action_running:
                LOGGER.info("TUI action skipped while busy: %s screen_depth=%s", action_name, len(self.screen_stack))
                return
            self.form_action_running = True
            LOGGER.info("TUI action requested: %s screen_depth=%s", action_name, len(self.screen_stack))
            try:
                self.run_action_worker(action_name, action)
            except Exception:
                self.form_action_running = False
                raise

        @work(group="form-actions", exit_on_error=False)
        async def run_action_worker(self, action_name: str, action: Callable[[], Awaitable[None]]) -> None:
            LOGGER.info("TUI action start: %s screen_depth=%s", action_name, len(self.screen_stack))
            try:
                await action()
            except asyncio.CancelledError:
                LOGGER.info("TUI action cancelled: %s", action_name)
                raise
            except UserError as exc:
                self.notify_user_error(exc)
            except Exception as exc:
                self.notify_internal_error(action_name, exc)
            finally:
                self.form_action_running = False
                LOGGER.info("TUI action end: %s screen_depth=%s", action_name, len(self.screen_stack))

        def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
            worker = event.worker
            worker_group = getattr(worker, "group", "") or ""
            if worker_group != "form-actions":
                return
            worker_name = getattr(worker, "name", "") or "form-actions"
            LOGGER.info("TUI worker state: %s -> %s", worker_name, event.state.name)
            worker_error = getattr(worker, "error", None)
            if event.state == WorkerState.ERROR and worker_error is not None:
                log_exception(f"TUI worker error {worker_name}", worker_error)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            actions = {
                "prev-page": self.action_prev_page,
                "next-page": self.action_next_page,
            }
            action = actions.get(event.button.id or "")
            if action:
                action()

        def action_focus_search(self) -> None:
            self.query_one("#search-input", Input).focus()

        def action_refresh_data(self) -> None:
            LOGGER.info("TUI refresh requested")
            self.close_connection()
            self.conn = connect(self.db_path)
            self.refresh_all()
            self.notify("Data refreshed", severity="information")

        def action_prev_page(self) -> None:
            kind = self.active_recent_kind()
            if not kind or self.pages[kind] <= 0:
                return
            self.pages[kind] -= 1
            self.refresh_recent_table(kind)
            self.refresh_page_bar()

        def action_next_page(self) -> None:
            kind = self.active_recent_kind()
            if not kind or not self.has_next_page[kind]:
                return
            self.pages[kind] += 1
            self.refresh_recent_table(kind)
            self.refresh_page_bar()

        async def open_form(self, title: str, fields: list[dict[str, Any]], submit_label: str = "Save") -> dict[str, str] | None:
            screen_depth = len(self.screen_stack)
            if screen_depth > 1:
                LOGGER.info("TUI form skipped because another modal is already open: %s screen_depth=%s", title, screen_depth)
                return None
            LOGGER.info("TUI form push start: %s screen_depth=%s", title, screen_depth)
            try:
                result = await self.push_screen_wait(RecordFormScreen(title, fields, submit_label=submit_label))
            except asyncio.CancelledError:
                LOGGER.info("TUI form wait cancelled: %s", title)
                raise
            except Exception as exc:
                log_exception(f"TUI form wait {title}", exc)
                raise
            LOGGER.info("TUI form dismissed: %s submitted=%s", title, result is not None)
            return result

        def action_add_creator(self) -> None:
            self.start_action_worker("add creator", self._action_add_creator)

        async def _action_add_creator(self) -> None:
            data = await self.open_form(
                "Add Creator",
                [
                    {"name": "name", "label": "Name", "placeholder": "creator name"},
                    {"name": "url", "label": "First URL", "placeholder": "https://..."},
                    {"name": "platform", "label": "Platform", "placeholder": "optional"},
                    {"name": "pid", "label": "Platform ID", "placeholder": "optional"},
                    {"name": "note", "label": "Note", "kind": "textarea", "placeholder": "optional"},
                ],
                submit_label="Create",
            )
            if not data:
                return
            if not data["name"]:
                self.notify("Name is required", severity="error")
                return
            try:
                result = add_creator_record(
                    self.conn, self.db_path, data["name"],
                    url=data["url"] or None,
                    platform=data["platform"] or None,
                    platform_id=data["pid"] or None,
                    note=data["note"] or None,
                )
                self.refresh_all()
                self.show_detail("creator", int(result["creator_id"]))
                self.notify(f"Added {creator_label(self.conn, int(result['creator_id']))}", severity="information")
            except UserError as exc:
                self.notify_user_error(exc)

        def action_add_name(self) -> None:
            self.start_action_worker("add name", self._action_add_name)

        async def _action_add_name(self) -> None:
            data = await self.open_form(
                "Add Name Fact",
                [
                    {"name": "target", "label": "Target", "value": self.current_target(), "placeholder": "#id / name / URL / platform:pid"},
                    {"name": "name", "label": "Name", "placeholder": "new name"},
                    {"name": "platform", "label": "Platform", "placeholder": "optional"},
                    {"name": "pid", "label": "Platform ID", "placeholder": "optional"},
                    {"name": "url", "label": "URL", "placeholder": "optional"},
                    {"name": "from_name", "label": "From Name", "placeholder": "optional"},
                    {"name": "reason", "label": "Reason", "placeholder": "same-person / rename / new-account"},
                    {"name": "status", "label": "Status", "value": "active"},
                    {"name": "note", "label": "Note", "kind": "textarea", "placeholder": "optional"},
                ],
            )
            if not data:
                return
            if not data["target"] or not data["name"]:
                self.notify("Target and name are required", severity="error")
                return
            try:
                result = add_name_record(
                    self.conn, self.db_path, data["target"], data["name"],
                    platform=data["platform"] or None,
                    platform_id=data["pid"] or None,
                    url=data["url"] or None,
                    from_name=data["from_name"] or None,
                    reason=data["reason"] or None,
                    status=data["status"] or "active",
                    note=data["note"] or None,
                )
                self.refresh_all()
                self.show_detail("creator", int(result["creator_id"]))
                self.notify("Name fact added", severity="information")
            except UserError as exc:
                self.notify_user_error(exc)

        def action_add_url(self) -> None:
            self.start_action_worker("add url", self._action_add_url)

        async def _action_add_url(self) -> None:
            data = await self.open_form(
                "Add URL Fact",
                [
                    {"name": "target", "label": "Target", "value": self.current_target(), "placeholder": "#id / name / URL / platform:pid"},
                    {"name": "url", "label": "URL", "placeholder": "https://..."},
                    {"name": "platform", "label": "Platform", "placeholder": "optional"},
                    {"name": "pid", "label": "Platform ID", "placeholder": "optional"},
                    {"name": "name", "label": "Display Name", "placeholder": "optional"},
                    {"name": "from_url", "label": "From URL", "placeholder": "optional"},
                    {"name": "reason", "label": "Reason", "placeholder": "new-platform / moved / banned / inactive"},
                    {"name": "status", "label": "Status", "value": "active"},
                    {"name": "note", "label": "Note", "kind": "textarea", "placeholder": "optional"},
                ],
            )
            if not data:
                return
            if not data["target"] or not data["url"]:
                self.notify("Target and URL are required", severity="error")
                return
            try:
                result = add_url_record(
                    self.conn, self.db_path, data["target"], data["url"],
                    platform=data["platform"] or None,
                    platform_id=data["pid"] or None,
                    name=data["name"] or None,
                    from_url=data["from_url"] or None,
                    reason=data["reason"] or None,
                    status=data["status"] or "active",
                    note=data["note"] or None,
                )
                self.refresh_all()
                self.show_detail("creator", int(result["creator_id"]))
                self.notify("URL fact added", severity="information")
            except UserError as exc:
                self.notify_user_error(exc)

        def action_add_post(self) -> None:
            self.start_action_worker("record post", self._action_add_post)

        async def _action_add_post(self) -> None:
            data = await self.open_form(
                "Record Post",
                [
                    {"name": "url", "label": "Post URL", "placeholder": "https://..."},
                    {"name": "target", "label": "Target", "value": self.current_target(), "placeholder": "optional if metadata can identify creator"},
                    {"name": "note", "label": "Note", "kind": "textarea", "placeholder": "optional"},
                    {"name": "timeout", "label": "Timeout Seconds", "value": "60"},
                ],
                submit_label="Record",
            )
            if not data:
                return
            if not data["url"]:
                self.notify("Post URL is required", severity="error")
                return
            try:
                timeout = int(data["timeout"] or "60")
            except ValueError:
                self.notify("Timeout must be an integer", severity="error")
                return
            self.status("Recording post metadata...")
            try:
                result = add_post_record(
                    self.conn, self.db_path, data["url"],
                    target=data["target"] or None,
                    note=data["note"] or None,
                    timeout=timeout,
                )
                self.refresh_all()
                self.show_detail("post", int(result["post_id"]))
                if result["warning"]:
                    self.notify(result["warning"], severity="warning", timeout=6)
                else:
                    self.notify("Post recorded", severity="information")
            except UserError as exc:
                self.notify_user_error(exc)
            finally:
                self.status("Ready")

        def action_add_reminder(self) -> None:
            self.start_action_worker("set reminder", self._action_add_reminder)

        async def _action_add_reminder(self) -> None:
            data = await self.open_form(
                "Set Reminder",
                [
                    {"name": "target", "label": "Target", "value": self.current_target(), "placeholder": "#id / name / URL / platform:pid"},
                    {"name": "when", "label": "When", "value": "30d", "placeholder": "YYYY-MM-DD / today / tomorrow / Nd"},
                    {"name": "interval_days", "label": "Interval Days", "placeholder": "optional"},
                    {"name": "note", "label": "Note", "kind": "textarea", "placeholder": "optional"},
                ],
                submit_label="Save Reminder",
            )
            if not data:
                return
            if not data["target"] or not data["when"]:
                self.notify("Target and when are required", severity="error")
                return
            interval_days = None
            if data["interval_days"]:
                try:
                    interval_days = int(data["interval_days"])
                except ValueError:
                    self.notify("Interval days must be an integer", severity="error")
                    return
            try:
                result = set_reminder_record(
                    self.conn, data["target"], data["when"],
                    interval_days=interval_days,
                    note=data["note"] or None,
                )
                self.refresh_all()
                self.show_detail("creator", int(result["creator_id"]))
                self.notify(f"Reminder set for {result['due_at']}", severity="information")
            except UserError as exc:
                self.notify_user_error(exc)

        def action_add_work(self) -> None:
            self.start_action_worker("add work", self._action_add_work)

        async def _action_add_work(self) -> None:
            data = await self.open_form(
                "Add Work Log",
                [
                    {"name": "target", "label": "Target", "value": self.current_target(), "placeholder": "#id / name / URL / platform:pid"},
                    {"name": "message", "label": "Message", "kind": "textarea", "placeholder": "what you did"},
                    {"name": "tags", "label": "Tags", "placeholder": "comma or newline separated"},
                    {"name": "paths", "label": "Paths", "kind": "textarea", "placeholder": "comma or newline separated"},
                    {"name": "urls", "label": "URLs", "kind": "textarea", "placeholder": "comma or newline separated"},
                    {"name": "meta", "label": "Metadata JSON", "kind": "textarea", "placeholder": "optional JSON"},
                ],
                submit_label="Record Work",
            )
            if not data:
                return
            if not data["target"] or not data["message"]:
                self.notify("Target and message are required", severity="error")
                return
            metadata = None
            if data["meta"]:
                try:
                    metadata = json.loads(data["meta"])
                except json.JSONDecodeError:
                    self.notify("Metadata JSON is invalid", severity="error")
                    return
            try:
                result = add_work_record(
                    self.conn, data["target"], data["message"],
                    tags=split_values(data["tags"]),
                    paths=split_values(data["paths"]),
                    urls=split_values(data["urls"]),
                    metadata=metadata,
                )
                self.refresh_all()
                self.show_detail("work", int(result["work_id"]))
                self.notify("Work log added", severity="information")
            except UserError as exc:
                self.notify_user_error(exc)


def launch_tui(db_path: Path) -> int:
    import sys
    ensure_textual()
    if not sys.stdout.isatty():
        raise UserError("Interactive mode requires a terminal. Use a CLI command when running non-interactively.")
    app = ClogApp(db_path)  # type: ignore[misc]
    try:
        LOGGER.info("TUI launch db=%s", db_path)
        app.run()
    except Exception as exc:
        log_exception("TUI fatal", exc)
        raise
    finally:
        app.close_connection()
        LOGGER.info("TUI exit db=%s", db_path)
    return 0
