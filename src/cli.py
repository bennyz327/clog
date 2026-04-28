from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .config import LOGGER, config_path, log_path, parse_global_options, write_default_config
from .constants import AmbiguousTarget, UserError
from .db import (
    candidate_lines,
    connect,
    creator_label,
    due_rows,
    fetch_post_detail_row,
    fetch_work_detail_row,
    grouped_search,
    recent_creators,
    recent_posts,
    recent_worklogs,
    rebuild_search,
    resolve_target,
    search_rows,
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
from .tui import launch_tui
from .utils import json_loads, make_parser, parse_known, prompt_input, shorten
from .worker import process_metadata_tasks
from .db import fetch_creator_snapshot


# ── print helpers ─────────────────────────────────────────────────────────────

def print_grouped_search(groups: list[dict[str, Any]]) -> None:
    for index, group in enumerate(groups, start=1):
        creator = group["creator"]
        print(f"{index}. #{creator['id']} {creator['primary_name']} [{creator['status']}]")
        matches = format_kind_counts(group["kind_counts"])
        if matches:
            print(f"   matches: {matches}")
        if group["names"]:
            print(f"   names: {', '.join(group['names'])}")
        if group["accounts"]:
            print(f"   accounts: {', '.join(group['accounts'])}")
        if group["urls"]:
            url_text = "; ".join(
                f"{platform or 'url'} {shorten(url, 80)}"
                for platform, url in group["urls"]
            )
            print(f"   urls: {url_text}")
        if group["snippets"]:
            print(f"   hits: {'; '.join(group['snippets'])}")


def show_search_selection(
    conn: sqlite3.Connection, db_path: Path, groups: list[dict[str, Any]]
) -> None:
    if not sys.stdin.isatty():
        print("use `clog v #id` to show a main record")
        return
    choice = prompt_input("show which result? number/#id, Enter to exit: ").strip()
    if not choice:
        return
    if choice.startswith("#"):
        creator_id = resolve_target(conn, choice)
    else:
        try:
            index = int(choice)
        except ValueError as exc:
            raise UserError("selection must be a result number or #id") from exc
        if index < 1 or index > len(groups):
            raise UserError("selection is out of range")
        creator_id = int(groups[index - 1]["creator"]["id"])
    print("")
    cmd_show(conn, db_path, [f"#{creator_id}"])


def print_due(conn: sqlite3.Connection, include_future: bool = False) -> None:
    rows = due_rows(conn, include_future=include_future)
    if not rows:
        print("no due reminders")
        return
    for row in rows:
        interval = f", every {row['interval_days']}d" if row["interval_days"] else ""
        note = f" | {row['note']}" if row["note"] else ""
        print(f"#{row['creator_id']} {row['primary_name']} due {row['next_due_at']}{interval}{note}")


def print_recent_creators(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("  (none)")
        return
    for index, row in enumerate(rows, start=1):
        print(
            f"  {index}. #{row['id']} {row['primary_name']} [{row['status']}] "
            f"updated={row['updated_at']} urls={row['url_count']} posts={row['post_count']} work={row['work_count']}"
        )


def print_recent_posts(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("  (none)")
        return
    for index, row in enumerate(rows, start=1):
        title = row["title"] or row["url"]
        platform = f"{row['platform']} " if row["platform"] else ""
        print(
            f"  {index}. post:{row['id']} #{row['creator_id']} {row['primary_name']} "
            f"{platform}{shorten(title, 90)} captured={row['captured_at']}"
        )


def print_recent_worklogs(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("  (none)")
        return
    for index, row in enumerate(rows, start=1):
        tags = json_loads(row["tags_json"], [])
        tag_text = f" tags={','.join(tags)}" if tags else ""
        print(
            f"  {index}. work:{row['id']} #{row['creator_id']} {row['primary_name']} "
            f"{shorten(row['message'], 90)}{tag_text} at={row['created_at']}"
        )


def print_recent_summary(conn: sqlite3.Connection, limit: int) -> None:
    print("recent creators:")
    print_recent_creators(recent_creators(conn, limit))
    print("")
    print("recent posts:")
    print_recent_posts(recent_posts(conn, limit))
    print("")
    print("recent work:")
    print_recent_worklogs(recent_worklogs(conn, limit))


def print_json_detail(label: str, text: str | None, limit: int = 5000) -> None:
    value = json_loads(text, None)
    if value is None:
        return
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    print(f"{label}:")
    print(shorten(rendered, limit))


def print_post_detail(conn: sqlite3.Connection, post_id: int) -> None:
    print(render_post_detail(fetch_post_detail_row(conn, post_id)))


def print_work_detail(conn: sqlite3.Connection, work_id: int) -> None:
    print(render_work_detail(fetch_work_detail_row(conn, work_id)))


def print_table_rows(rows: list[sqlite3.Row], columns: list[str]) -> None:
    if not rows:
        print("  (none)")
        return
    for row in rows:
        parts = []
        for col in columns:
            value = row[col]
            if value not in (None, ""):
                parts.append(f"{col}={shorten(value, 80)}")
        print("  " + " | ".join(parts))


def print_help() -> None:
    print(
        """clog - creator-centric local tracker

Usage:
  clog                  launch interactive mode
  clog [--db PATH] COMMAND ...
  clog QUERY

Commands:
  i, init             initialize config and database
  a, add NAME [URL]   add creator
  n, name TARGET NAME add name fact
  u, url TARGET URL   add URL fact
  p, post URL [TARGET]
                      record post metadata with gallery-dl when available
  r, remind TARGET WHEN
                      set reminder; WHEN = YYYY-MM-DD, today, tomorrow, Nd
  d, due              show due reminders
  ls, l               recent creators/posts/work with interactive browser
  c, work TARGET MSG  add commit-like work log
  s, search QUERY     grouped global search (-i choose, -v show if unique, --raw debug)
  v, show TARGET      show creator main record

Target forms:
  #12
  https://platform/profile
  platform:platform_id
  exact or fuzzy name if it resolves to exactly one creator
"""
    )


# ── interactive browse ────────────────────────────────────────────────────────

def browse_recent_list(
    conn: sqlite3.Connection,
    db_path: Path,
    kind: str,
    page_size: int,
) -> bool:
    offset = 0
    while True:
        rows = {
            "creators": recent_creators,
            "posts": recent_posts,
            "work": recent_worklogs,
        }[kind](conn, page_size + 1, offset)
        page_rows = rows[:page_size]
        has_next = len(rows) > page_size
        print("")
        print(f"{kind} page {offset // page_size + 1}:")
        if kind == "creators":
            print_recent_creators(page_rows)
        elif kind == "posts":
            print_recent_posts(page_rows)
        else:
            print_recent_worklogs(page_rows)
        prompt = "number=view"
        if has_next:
            prompt += ", n=next"
        if offset > 0:
            prompt += ", p=prev"
        prompt += ", b=back, q=quit: "
        choice = prompt_input(prompt, "b").strip().lower()
        if choice in ("b", "back", ""):
            return False
        if choice in ("q", "quit"):
            return True
        if choice in ("n", "next") and has_next:
            offset += page_size
            continue
        if choice in ("p", "prev") and offset > 0:
            offset = max(0, offset - page_size)
            continue
        try:
            index = int(choice)
        except ValueError:
            print("invalid choice")
            continue
        if index < 1 or index > len(page_rows):
            print("selection is out of range")
            continue
        row = page_rows[index - 1]
        print("")
        if kind == "creators":
            cmd_show(conn, db_path, [f"#{row['id']}"])
        elif kind == "posts":
            print_post_detail(conn, int(row["id"]))
        else:
            print_work_detail(conn, int(row["id"]))
        prompt_input("press Enter to return to list...")
    raise AssertionError("browse_recent_list loop should not exit")


def interactive_ls(conn: sqlite3.Connection, db_path: Path, page_size: int) -> None:
    while True:
        print("")
        choice = prompt_input("open [c]reators, [p]osts, [w]ork, [q]uit: ", "q").strip().lower()
        if choice in ("q", "quit", ""):
            return
        if choice in ("c", "creator", "creators"):
            if browse_recent_list(conn, db_path, "creators", page_size):
                return
        elif choice in ("p", "post", "posts"):
            if browse_recent_list(conn, db_path, "posts", page_size):
                return
        elif choice in ("w", "work", "worklogs"):
            if browse_recent_list(conn, db_path, "work", page_size):
                return
        else:
            print("invalid choice")


# ── CLI commands ──────────────────────────────────────────────────────────────

def cmd_init(conn: sqlite3.Connection, db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog init")
    parse_known(parser, args)
    write_default_config()
    rebuild_search(conn)
    conn.commit()
    print(f"initialized: {db_path}")
    print(f"config: {config_path()}")


def cmd_add(conn: sqlite3.Connection, db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog add")
    parser.add_argument("name")
    parser.add_argument("url", nargs="?")
    parser.add_argument("-p", "--platform")
    parser.add_argument("--pid")
    parser.add_argument("--note")
    ns = parse_known(parser, args)
    result = add_creator_record(
        conn, db_path, ns.name,
        url=ns.url, platform=ns.platform, platform_id=ns.pid, note=ns.note,
    )
    print(f"added {creator_label(conn, result['creator_id'])}")


def cmd_name(conn: sqlite3.Connection, db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog name")
    parser.add_argument("target")
    parser.add_argument("name")
    parser.add_argument("-p", "--platform")
    parser.add_argument("--pid")
    parser.add_argument("-u", "--url")
    parser.add_argument("--from", dest="from_name")
    parser.add_argument("-r", "--reason")
    parser.add_argument("--status", default="active")
    parser.add_argument("--note")
    ns = parse_known(parser, args)
    result = add_name_record(
        conn, db_path, ns.target, ns.name,
        platform=ns.platform, platform_id=ns.pid,
        url=ns.url, from_name=ns.from_name,
        reason=ns.reason, status=ns.status, note=ns.note,
    )
    print(f"added name #{result['name_id']} to {creator_label(conn, result['creator_id'])}")


def cmd_url(conn: sqlite3.Connection, db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog url")
    parser.add_argument("target")
    parser.add_argument("url")
    parser.add_argument("-p", "--platform")
    parser.add_argument("--pid")
    parser.add_argument("--from", dest="from_url")
    parser.add_argument("-r", "--reason")
    parser.add_argument("--status", default="active")
    parser.add_argument("--name")
    parser.add_argument("--note")
    ns = parse_known(parser, args)
    result = add_url_record(
        conn, db_path, ns.target, ns.url,
        platform=ns.platform, platform_id=ns.pid,
        name=ns.name, from_url=ns.from_url,
        reason=ns.reason, status=ns.status, note=ns.note,
    )
    print(f"added url #{result['url_id']} to {creator_label(conn, result['creator_id'])}")


def cmd_post(conn: sqlite3.Connection, db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog post")
    parser.add_argument("url")
    parser.add_argument("target", nargs="?")
    parser.add_argument("--note")
    parser.add_argument("--timeout", type=int, default=60)
    ns = parse_known(parser, args)
    result = add_post_record(
        conn, db_path, ns.url,
        target=ns.target, note=ns.note, timeout=ns.timeout,
    )
    if result["warning"]:
        print(f"warning: {result['warning']}")
    print(f"recorded post #{result['post_id']} for {creator_label(conn, result['creator_id'])}")


def cmd_remind(conn: sqlite3.Connection, _db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog remind")
    parser.add_argument("target")
    parser.add_argument("when")
    parser.add_argument("-i", "--interval-days", type=int)
    parser.add_argument("--note")
    ns = parse_known(parser, args)
    result = set_reminder_record(
        conn, ns.target, ns.when,
        interval_days=ns.interval_days, note=ns.note,
    )
    print(f"reminder set for {creator_label(conn, result['creator_id'])}: {result['due_at']}")


def cmd_due(conn: sqlite3.Connection, _db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog due")
    parser.add_argument("--all", action="store_true")
    ns = parse_known(parser, args)
    print_due(conn, include_future=ns.all)


def cmd_ls(conn: sqlite3.Connection, db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog ls")
    parser.add_argument("-n", "--limit", type=int, default=5)
    parser.add_argument("--page-size", type=int, default=10)
    parser.add_argument("-i", "--interactive", action="store_true")
    parser.add_argument("--no-interactive", action="store_true")
    ns = parse_known(parser, args)
    limit = max(1, ns.limit)
    page_size = max(1, ns.page_size)
    print_recent_summary(conn, limit)
    if not ns.no_interactive and (ns.interactive or sys.stdin.isatty()):
        interactive_ls(conn, db_path, page_size)


def cmd_work(conn: sqlite3.Connection, _db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog work")
    parser.add_argument("target")
    parser.add_argument("message")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--meta")
    ns = parse_known(parser, args)
    metadata = None
    if ns.meta:
        try:
            metadata = json.loads(ns.meta)
        except json.JSONDecodeError as exc:
            raise UserError("--meta must be valid JSON") from exc
    result = add_work_record(
        conn, ns.target, ns.message,
        tags=ns.tag, paths=ns.path, urls=ns.url, metadata=metadata,
    )
    print(f"recorded work #{result['work_id']} for {creator_label(conn, result['creator_id'])}")


def cmd_search(conn: sqlite3.Connection, db_path: Path, args: list[str]) -> None:
    interactive = False
    view = False
    raw = False
    query_parts: list[str] = []
    for arg in args:
        if arg in ("-i", "--interactive"):
            interactive = True
        elif arg in ("-v", "--view"):
            view = True
        elif arg == "--raw":
            raw = True
        else:
            query_parts.append(arg)
    query = " ".join(query_parts).strip()
    if not query:
        raise UserError("search requires QUERY")
    if raw:
        rows = search_rows(conn, query)
        if not rows:
            print("no results")
            return
        for row in rows:
            label = creator_label(conn, int(row["creator_id"]))
            title = shorten(row["title"], 80)
            body = shorten(row["body"], 140)
            print(f"[{row['kind']}] {label} :: {title}")
            if body and body != title:
                print(f"  {body}")
        return
    groups = grouped_search(conn, query)
    if not groups:
        print("no results")
        return
    print_grouped_search(groups)
    if view:
        if len(groups) != 1:
            raise UserError("`-v/--view` requires exactly one matched main record")
        print("")
        cmd_show(conn, db_path, [f"#{groups[0]['creator']['id']}"])
    elif interactive:
        show_search_selection(conn, db_path, groups)
    else:
        print("use `clog s QUERY -i` to choose, or `clog v #id` to show a main record")


def cmd_show(conn: sqlite3.Connection, _db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog show")
    parser.add_argument("target")
    ns = parse_known(parser, args)
    creator_id = resolve_target(conn, ns.target)
    print(render_creator_snapshot(fetch_creator_snapshot(conn, creator_id)))


def cmd_home(conn: sqlite3.Connection) -> None:
    due = due_rows(conn)
    if due:
        print("due reminders:")
        for row in due[:10]:
            note = f" | {row['note']}" if row["note"] else ""
            print(f"  #{row['creator_id']} {row['primary_name']} due {row['next_due_at']}{note}")
        if len(due) > 10:
            print(f"  ... and {len(due) - 10} more")
    else:
        print("no due reminders")
    print("")
    print("quick commands:")
    print("  clog a NAME [URL]              add creator")
    print("  clog n TARGET NAME             add name fact")
    print("  clog u TARGET URL              add URL fact")
    print("  clog p URL [TARGET]            record post metadata")
    print("  clog r TARGET WHEN             set reminder")
    print("  clog c TARGET MESSAGE          add work log")
    print("  clog ls                        recent lists + interactive browser")
    print("  clog s QUERY / clog QUERY      global search")
    print("  clog v TARGET                  show creator")


# ── dispatch ──────────────────────────────────────────────────────────────────

CommandHandler = Any

COMMANDS: dict[str, tuple[str, CommandHandler]] = {
    "init": ("init", cmd_init),
    "i": ("init", cmd_init),
    "add": ("add", cmd_add),
    "a": ("add", cmd_add),
    "name": ("name", cmd_name),
    "n": ("name", cmd_name),
    "url": ("url", cmd_url),
    "u": ("url", cmd_url),
    "post": ("post", cmd_post),
    "p": ("post", cmd_post),
    "remind": ("remind", cmd_remind),
    "r": ("remind", cmd_remind),
    "due": ("due", cmd_due),
    "d": ("due", cmd_due),
    "ls": ("ls", cmd_ls),
    "l": ("ls", cmd_ls),
    "work": ("work", cmd_work),
    "c": ("work", cmd_work),
    "search": ("search", cmd_search),
    "s": ("search", cmd_search),
    "show": ("show", cmd_show),
    "v": ("show", cmd_show),
}


def dispatch(db_path: Path, _quiet: bool, argv: list[str]) -> int:
    LOGGER.info("Dispatch db=%s argv=%s", db_path, argv)
    if argv and argv[0] == "__meta_worker":
        parser = make_parser("clog __meta_worker")
        parser.add_argument("--limit", type=int, default=5)
        ns = parse_known(parser, argv[1:])
        process_metadata_tasks(db_path, ns.limit)
        return 0

    if argv and argv[0] in ("-h", "--help", "help"):
        print_help()
        return 0
    if not argv:
        return launch_tui(db_path)
    if len(argv) >= 2 and argv[1] in ("-h", "--help") and argv[0] in COMMANDS:
        canonical, handler = COMMANDS[argv[0]]
        if canonical == "search":
            print_help()
            return 0
        handler(None, db_path, argv[1:])
        return 0

    conn = connect(db_path)
    try:
        name = argv[0]
        if name not in COMMANDS:
            cmd_search(conn, db_path, argv)
            return 0
        _, handler = COMMANDS[name]
        handler(conn, db_path, argv[1:])
        return 0
    finally:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as exc:
            LOGGER.warning("CLI DB checkpoint failed: %s", exc)
        conn.close()
        LOGGER.info("CLI DB closed %s", db_path)


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        db_path, quiet, rest = parse_global_options(effective_argv)
        return dispatch(db_path, quiet, rest)
    except AmbiguousTarget as exc:
        LOGGER.warning("Ambiguous target: %s", exc.target)
        try:
            db_path, _, _ = parse_global_options(effective_argv)
            conn = connect(db_path)
            try:
                print(f"ambiguous target: {exc.target}", file=sys.stderr)
                print("use #id, URL, or platform:platform_id:", file=sys.stderr)
                for line in candidate_lines(conn, exc.creator_ids):
                    print(line, file=sys.stderr)
            finally:
                conn.close()
        except Exception:
            print(str(exc), file=sys.stderr)
        return 2
    except UserError as exc:
        LOGGER.warning("User error: %s", exc)
        message = str(exc)
        if message:
            print(f"error: {message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user")
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        from .config import log_exception
        log_exception("Fatal error", exc)
        print(f"error: unexpected failure, see {log_path().name}", file=sys.stderr)
        return 1
