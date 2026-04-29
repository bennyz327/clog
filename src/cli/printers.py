from __future__ import annotations

import json
import sqlite3
from typing import Any

from core.render import (
    format_kind_counts,
    render_post_detail,
    render_work_detail,
)
from core.utils import json_loads, shorten
from db import (
    due_rows,
    fetch_post_detail_row,
    fetch_work_detail_row,
    profile_brief,
    recent_creators,
    recent_posts,
    recent_worklogs,
)


def print_grouped_search(groups: list[dict[str, Any]]) -> None:
    for index, group in enumerate(groups, start=1):
        creator = group["creator"]
        print(f"{index}. #{creator['id']} {creator['primary_name']} [{creator['status']}]")
        matches = format_kind_counts(group["kind_counts"])
        if matches:
            print(f"   matches: {matches}")
        if group["aliases"]:
            print(f"   aliases: {', '.join(group['aliases'])}")
        if group["profiles"]:
            print(f"   profiles: {'; '.join(group['profiles'])}")
        if group["urls"]:
            url_text = "; ".join(shorten(url, 80) for url in group["urls"])
            print(f"   urls: {url_text}")
        if group["snippets"]:
            print(f"   hits: {'; '.join(group['snippets'])}")


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
            f"updated={row['updated_at']} profiles={row['profile_count']} urls={row['url_count']} "
            f"posts={row['post_count']} work={row['work_count']}"
        )


def print_recent_posts(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("  (none)")
        return
    for index, row in enumerate(rows, start=1):
        title = row["title"] or row["url"]
        profile = profile_brief(row["platform"], row["platform_id"], row["display_name"])
        print(
            f"  {index}. post:{row['id']} #{row['creator_id']} {row['primary_name']} "
            f"[{profile}] {shorten(title, 90)} captured={row['captured_at']}"
        )


def print_recent_worklogs(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("  (none)")
        return
    for index, row in enumerate(rows, start=1):
        print(
            f"  {index}. work:{row['id']} #{row['creator_id']} {row['primary_name']} "
            f"{shorten(row['content'], 90)} at={row['created_at']}"
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
  clog [--db PATH] COMMAND ...
  clog QUERY

Commands:
  i, init             initialize config and database
  a, add NAME [URL]   add creator
  n, name TARGET NAME [CONTEXT]
                      add generic alias, rename an existing profile, or add via creator URL
  u, url TARGET URL   attach creator URL to a profile
  p, post URL [TARGET]
                      record post only when metadata matches an existing profile
  r, remind TARGET WHEN
                      set reminder; WHEN = YYYY-MM-DD, today, tomorrow, Nd
  d, due              show due reminders
  ls, l               recent creators/posts/work with interactive browser
  c, work TARGET CONTENT
                      add creator-level work log content
  s, search QUERY     grouped global search (-i choose, -v show if unique, --raw debug)
  v, show TARGET      show creator main record

Target forms:
  #12
  https://platform/profile
  platform:platform_id
  exact or fuzzy name if it resolves to exactly one creator
"""
    )
