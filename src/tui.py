from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any

from .config import LOGGER, log_path, log_exception
from .constants import AmbiguousTarget, UserError
from .db import (
    candidate_lines,
    connect,
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
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import (
        Button, DataTable, Footer, Header, Input,
        Static, TabbedContent, TabPane, TextArea,
    )
except Exception as _exc:
    App = None  # type: ignore[assignment]
    ComposeResult = Any  # type: ignore[assignment]
    Binding = Any  # type: ignore[assignment]
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
            if not self.fields:
                return
            self.call_after_refresh(self.focus_first_field)

        def focus_first_field(self) -> None:
            if not self.fields:
                return
            first_id = f"#field-{self.fields[0]['name']}"
            try:
                self.query_one(first_id).focus()
            except Exception:
                pass

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
            LOGGER.info("TUI form submit: %s", self.title)
            self.dismiss(data)

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

        #search-label {
            width: 9;
            content-align: center middle;
            color: #93c5fd;
            text-style: bold;
        }

        #search-input {
            width: 1fr;
        }

        #action-bar {
            height: auto;
            padding: 1;
            background: #101826;
            border-bottom: solid #22324a;
        }

        #action-bar Button {
            margin-right: 1;
            min-width: 11;
        }

        #body {
            layout: horizontal;
            height: 1fr;
        }

        #sidebar {
            width: 32;
            min-width: 28;
            padding: 1;
            background: #0f172a;
            border-right: solid #22324a;
        }

        #main {
            width: 1fr;
            padding: 1;
        }

        #detail {
            width: 46;
            min-width: 38;
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
            height: 12;
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
        """

        BINDINGS = [
            Binding("/", "focus_search", "Search", show=True),
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
            self.pending_tasks: set[asyncio.Task[Any]] = set()
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
                    yield Static("SEARCH", id="search-label")
                    yield Input(placeholder="Search creators, urls, platform ids, posts, work...", id="search-input")
                with Horizontal(id="action-bar"):
                    yield Button("Add Creator", id="btn-add", variant="primary")
                    yield Button("Add Name", id="btn-name")
                    yield Button("Add URL", id="btn-url")
                    yield Button("Record Post", id="btn-post")
                    yield Button("Remind", id="btn-remind")
                    yield Button("Add Work", id="btn-work")
                    yield Button("Refresh", id="btn-refresh")
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
            self.refresh_all()
            self.query_one("#search-input", Input).focus()

        def on_unmount(self) -> None:
            for task in list(self.pending_tasks):
                task.cancel()
            self.pending_tasks.clear()

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

        def set_detail(self, kind: str, entity_id: int) -> None:
            try:
                title = self.query_one("#detail-title", Static)
                body = self.query_one("#detail-content", Static)
            except Exception:
                self.current_detail_kind = kind
                self.current_detail_id = entity_id
                return
            if kind == "creator":
                snapshot = fetch_creator_snapshot(self.conn, entity_id)
                creator = snapshot["creator"]
                self.current_creator_id = int(creator["id"])
                self.current_detail_kind = kind
                self.current_detail_id = entity_id
                title.update(f"Creator #{creator['id']} {creator['primary_name']}")
                body.update(render_creator_snapshot(snapshot))
                return
            if kind == "post":
                row = fetch_post_detail_row(self.conn, entity_id)
                self.current_creator_id = int(row["creator_id"])
                self.current_detail_kind = kind
                self.current_detail_id = entity_id
                title.update(f"Post {row['id']} -> #{row['creator_id']} {row['primary_name']}")
                body.update(render_post_detail(row))
                return
            if kind == "work":
                row = fetch_work_detail_row(self.conn, entity_id)
                self.current_creator_id = int(row["creator_id"])
                self.current_detail_kind = kind
                self.current_detail_id = entity_id
                title.update(f"Work {row['id']} -> #{row['creator_id']} {row['primary_name']}")
                body.update(render_work_detail(row))

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
            self.notify(f"{action_name} failed. See {log_path().name}", severity="error", timeout=10)

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

        def handle_table_pick(self, table_id: str, row_key: Any) -> None:
            entity_id = self.row_key_value(row_key)
            if entity_id is None:
                return
            try:
                if table_id in {"due-table", "sidebar-creators", "search-table", "creators-table"}:
                    self.set_detail("creator", entity_id)
                elif table_id == "posts-table":
                    self.set_detail("post", entity_id)
                elif table_id == "work-table":
                    self.set_detail("work", entity_id)
            except UserError as exc:
                self.notify_user_error(exc)

        def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
            self.handle_table_pick(event.data_table.id or "", event.row_key)

        def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
            self.handle_table_pick(event.data_table.id or "", event.row_key)

        def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
            self.refresh_page_bar()

        def start_background_action(self, action_name: str, action: Any) -> None:
            async def runner() -> None:
                try:
                    result = action()
                    if inspect.isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    LOGGER.info("TUI action cancelled: %s", action_name)
                    raise
                except UserError as exc:
                    self.notify_user_error(exc)
                except Exception as exc:
                    self.notify_internal_error(action_name, exc)

            LOGGER.info("TUI action start: %s", action_name)
            task = asyncio.create_task(runner(), name=f"clog:{action_name}")
            self.pending_tasks.add(task)

            def _cleanup(done: asyncio.Task[Any]) -> None:
                self.pending_tasks.discard(done)
                if done.cancelled():
                    return
                exc = done.exception()
                if exc is not None and not isinstance(exc, UserError):
                    log_exception(f"TUI background task {action_name}", exc)
                LOGGER.info("TUI action end: %s", action_name)

            task.add_done_callback(_cleanup)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            actions = {
                "btn-add": self.action_add_creator,
                "btn-name": self.action_add_name,
                "btn-url": self.action_add_url,
                "btn-post": self.action_add_post,
                "btn-remind": self.action_add_reminder,
                "btn-work": self.action_add_work,
                "btn-refresh": self.action_refresh_data,
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
            if len(self.screen_stack) > 1:
                LOGGER.info("TUI form skipped because another modal is already open: %s", title)
                self.notify("Close the current dialog first", severity="warning", timeout=5)
                return None
            loop = asyncio.get_running_loop()
            future: asyncio.Future[dict[str, str] | None] = loop.create_future()

            def handle_dismiss(result: dict[str, str] | None) -> None:
                LOGGER.info("TUI form dismissed: %s", title)
                if not future.done():
                    future.set_result(result)

            LOGGER.info("TUI form open: %s", title)
            self.push_screen(RecordFormScreen(title, fields, submit_label=submit_label), callback=handle_dismiss)
            return await future

        def action_add_creator(self) -> None:
            self.start_background_action("add creator", self._action_add_creator)

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
                self.set_detail("creator", int(result["creator_id"]))
                from .db import creator_label
                self.notify(f"Added {creator_label(self.conn, int(result['creator_id']))}", severity="information")
            except UserError as exc:
                self.notify_user_error(exc)

        def action_add_name(self) -> None:
            self.start_background_action("add name", self._action_add_name)

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
                self.set_detail("creator", int(result["creator_id"]))
                self.notify("Name fact added", severity="information")
            except UserError as exc:
                self.notify_user_error(exc)

        def action_add_url(self) -> None:
            self.start_background_action("add url", self._action_add_url)

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
                self.set_detail("creator", int(result["creator_id"]))
                self.notify("URL fact added", severity="information")
            except UserError as exc:
                self.notify_user_error(exc)

        def action_add_post(self) -> None:
            self.start_background_action("record post", self._action_add_post)

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
                self.set_detail("post", int(result["post_id"]))
                if result["warning"]:
                    self.notify(result["warning"], severity="warning", timeout=6)
                else:
                    self.notify("Post recorded", severity="information")
            except UserError as exc:
                self.notify_user_error(exc)
            finally:
                self.status("Ready")

        def action_add_reminder(self) -> None:
            self.start_background_action("set reminder", self._action_add_reminder)

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
                self.set_detail("creator", int(result["creator_id"]))
                self.notify(f"Reminder set for {result['due_at']}", severity="information")
            except UserError as exc:
                self.notify_user_error(exc)

        def action_add_work(self) -> None:
            self.start_background_action("add work", self._action_add_work)

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
                self.set_detail("work", int(result["work_id"]))
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
    finally:
        app.close_connection()
        LOGGER.info("TUI exit db=%s", db_path)
    return 0
