from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

from core.background_handlers import run_background_job_inline
from core.background_runtime import BackgroundEnqueueApi
from core.constants import UserError
from core.config import config_path, write_default_config
from core.render import render_creator_snapshot
from core.service import (
    add_creator_record,
    add_name_record,
    add_post_record,
    add_url_record,
    add_work_record,
    set_reminder_record,
)
from core.utils import make_parser, parse_known, shorten
from db import (
    creator_label,
    due_rows,
    fetch_creator_snapshot,
    grouped_search,
    rebuild_search,
    recent_creators,
    recent_posts,
    recent_worklogs,
    resolve_target,
    search_rows,
)
from .printers import (
    print_due,
    print_grouped_search,
    print_post_detail,
    print_recent_creators,
    print_recent_posts,
    print_recent_summary,
    print_recent_worklogs,
    print_work_detail,
)
from .tty import prompt_input


def _show_creator(conn: sqlite3.Connection, creator_id: int) -> None:
    print(render_creator_snapshot(fetch_creator_snapshot(conn, creator_id)))


def _add_async_mode(parser) -> None:
    parser.add_argument(
        "--async-mode",
        choices=("inline", "enqueue"),
        default="inline",
        help="inline: finish in this CLI call; enqueue: store a background job for the GUI runtime",
    )


def _enqueue_job(
    db_path: Path,
    job_type: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    return BackgroundEnqueueApi(db_path).enqueue(
        job_type,
        payload,
        source="cli",
        dedupe_key=dedupe_key,
    )


def _print_enqueued(receipt: dict[str, Any], label: str) -> None:
    job_id = int(receipt["job_id"])
    if receipt.get("deduplicated"):
        print(f"{label} already queued as job #{job_id}")
    else:
        print(f"{label} queued as job #{job_id}")


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
    _show_creator(conn, creator_id)


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
            _show_creator(conn, int(row["id"]))
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
    parser.add_argument("--note")
    _add_async_mode(parser)
    ns = parse_known(parser, args)
    if ns.url and ns.async_mode == "enqueue":
        result = add_creator_record(
            conn,
            db_path,
            ns.name,
            url=ns.url,
            note=ns.note,
            resolve_metadata=False,
        )
        print(f"added {creator_label(conn, result['creator_id'])}")
        if result.get("needs_worker") and result.get("url_id") is not None:
            receipt = _enqueue_job(
                db_path,
                "profile.resolve_metadata",
                {"profile_url_id": int(result["url_id"])},
                dedupe_key=f"profile.resolve_metadata:{int(result['url_id'])}",
            )
            _print_enqueued(receipt, "profile metadata resolution")
        return
    if ns.url:
        result = run_background_job_inline(
            conn,
            db_path,
            "command.add_creator_with_url",
            {"name": ns.name, "url": ns.url, "note": ns.note},
        )
    else:
        result = add_creator_record(
            conn, db_path, ns.name,
            url=ns.url, note=ns.note,
        )
    if result.get("warning"):
        print(f"warning: {result['warning']}")
    print(f"added {creator_label(conn, result['creator_id'])}")


def cmd_name(conn: sqlite3.Connection, db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog name")
    parser.add_argument("target")
    parser.add_argument("name")
    parser.add_argument("context", nargs="?")
    _add_async_mode(parser)
    ns = parse_known(parser, args)
    if ns.context and ns.context.startswith(("http://", "https://")) and ns.async_mode == "enqueue":
        result = add_name_record(
            conn,
            db_path,
            ns.target,
            ns.name,
            context=ns.context,
            resolve_metadata=False,
        )
        print(f"added alias #{result['alias_id']} to {creator_label(conn, result['creator_id'])}")
        if result.get("needs_worker") and result.get("url_id") is not None:
            receipt = _enqueue_job(
                db_path,
                "profile.resolve_metadata",
                {"profile_url_id": int(result["url_id"])},
                dedupe_key=f"profile.resolve_metadata:{int(result['url_id'])}",
            )
            _print_enqueued(receipt, "profile metadata resolution")
        return
    if ns.context and ns.context.startswith(("http://", "https://")):
        result = run_background_job_inline(
            conn,
            db_path,
            "command.add_name_with_url",
            {"target": ns.target, "name": ns.name, "context": ns.context},
        )
    else:
        result = add_name_record(
            conn, db_path, ns.target, ns.name,
            context=ns.context,
        )
    if result.get("warning"):
        print(f"warning: {result['warning']}")
    print(f"added alias #{result['alias_id']} to {creator_label(conn, result['creator_id'])}")


def cmd_url(conn: sqlite3.Connection, db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog url")
    parser.add_argument("target")
    parser.add_argument("url")
    parser.add_argument("--note")
    _add_async_mode(parser)
    ns = parse_known(parser, args)
    if ns.async_mode == "enqueue":
        result = add_url_record(
            conn,
            db_path,
            ns.target,
            ns.url,
            note=ns.note,
            resolve_metadata=False,
        )
        print(f"attached url #{result['url_id']} to {creator_label(conn, result['creator_id'])}")
        if result.get("needs_worker") and result.get("url_id") is not None:
            receipt = _enqueue_job(
                db_path,
                "profile.resolve_metadata",
                {"profile_url_id": int(result["url_id"])},
                dedupe_key=f"profile.resolve_metadata:{int(result['url_id'])}",
            )
            _print_enqueued(receipt, "profile metadata resolution")
        return
    result = run_background_job_inline(
        conn,
        db_path,
        "command.add_url",
        {"target": ns.target, "url": ns.url, "note": ns.note},
    )
    if result.get("warning"):
        print(f"warning: {result['warning']}")
    print(f"attached url #{result['url_id']} to {creator_label(conn, result['creator_id'])}")


def cmd_post(conn: sqlite3.Connection, db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog post")
    parser.add_argument("url")
    parser.add_argument("target", nargs="?")
    parser.add_argument("--note")
    parser.add_argument("--timeout", type=int, default=60)
    _add_async_mode(parser)
    ns = parse_known(parser, args)
    payload = {
        "url": ns.url,
        "target": ns.target,
        "note": ns.note,
        "timeout": ns.timeout,
    }
    if ns.async_mode == "enqueue":
        receipt = _enqueue_job(
            db_path,
            "command.add_post",
            payload,
            dedupe_key=f"command.add_post:{ns.url}",
        )
        _print_enqueued(receipt, "add post")
        return
    result = run_background_job_inline(conn, db_path, "command.add_post", payload)
    if result.get("warning"):
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
    parser.add_argument("content")
    ns = parse_known(parser, args)
    result = add_work_record(
        conn, ns.target, ns.content,
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
        _show_creator(conn, int(groups[0]["creator"]["id"]))
    elif interactive:
        show_search_selection(conn, db_path, groups)
    else:
        print("use `clog s QUERY -i` to choose, or `clog v #id` to show a main record")


def cmd_show(conn: sqlite3.Connection, _db_path: Path, args: list[str]) -> None:
    parser = make_parser("clog show")
    parser.add_argument("target")
    ns = parse_known(parser, args)
    creator_id = resolve_target(conn, ns.target)
    _show_creator(conn, creator_id)


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
    print("  clog n TARGET NAME [CONTEXT]   add alias or profile rename")
    print("  clog u TARGET URL              attach creator URL")
    print("  clog p URL [TARGET]            record post against existing profile")
    print("  clog r TARGET WHEN             set reminder")
    print("  clog c TARGET CONTENT          add work log")
    print("  clog ls                        recent lists + interactive browser")
    print("  clog s QUERY / clog QUERY      global search")
    print("  clog v TARGET                  show creator")
