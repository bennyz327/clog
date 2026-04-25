from __future__ import annotations

import json
import sqlite3
from typing import Any

from .utils import json_loads, shorten


def format_kind_counts(kind_counts: dict[str, int]) -> str:
    order = ["creator", "account", "name", "url", "post", "work", "reminder"]
    parts: list[str] = []
    for kind in order:
        count = kind_counts.get(kind)
        if count:
            parts.append(kind if count == 1 else f"{kind} x{count}")
    for kind, count in sorted(kind_counts.items()):
        if kind not in order:
            parts.append(kind if count == 1 else f"{kind} x{count}")
    return ", ".join(parts)


def _fmt_tags(json_val: Any) -> str:
    items = json_loads(json_val, [])
    if isinstance(items, list) and items:
        return " ".join(f"#{t}" for t in items if t)
    return ""


def render_creator_snapshot(snapshot: dict[str, Any]) -> str:
    creator = snapshot["creator"]
    lines = [f"#{creator['id']}  {creator['primary_name']}  [{creator['status']}]"]
    if creator["note"]:
        lines.append(f"  note: {creator['note']}")

    lines.append("")
    lines.append("── names ──────────────────────────────")
    names = snapshot["names"]
    if not names:
        lines.append("  (none)")
    else:
        for row in names:
            pid_part = (
                f"  ({row['platform']}:{row['platform_id']})" if row["platform"] and row["platform_id"]
                else (f"  ({row['platform']})" if row["platform"] else "")
            )
            lines.append(f"  [{row['id']}] {row['name']}{pid_part}")
            meta_parts = []
            if row["reason"] not in (None, ""):
                meta_parts.append(str(row["reason"]))
            if row["status"] not in (None, "", "active"):
                meta_parts.append(str(row["status"]))
            if row["from_name"] not in (None, ""):
                meta_parts.append(f"from: {row['from_name']}")
            if row["note"] not in (None, ""):
                meta_parts.append(f"note: {row['note']}")
            if meta_parts:
                lines.append(f"      {' · '.join(meta_parts)}")

    lines.append("")
    lines.append("── urls ────────────────────────────────")
    urls = snapshot["urls"]
    if not urls:
        lines.append("  (none)")
    else:
        for row in urls:
            lines.append(f"  [{row['id']}] {shorten(row['url'], 60)}")
            sub_parts = []
            if row["platform"] not in (None, ""):
                pid = f":{row['platform_id']}" if row["platform_id"] not in (None, "") else ""
                sub_parts.append(f"{row['platform']}{pid}")
            if row["status"] not in (None, "", "active"):
                sub_parts.append(str(row["status"]))
            if row["reason"] not in (None, ""):
                sub_parts.append(str(row["reason"]))
            if row["from_url"] not in (None, ""):
                sub_parts.append(f"from: {shorten(row['from_url'], 40)}")
            if row["note"] not in (None, ""):
                sub_parts.append(f"note: {row['note']}")
            if sub_parts:
                lines.append(f"      {' · '.join(sub_parts)}")

    lines.append("")
    lines.append("── platform accounts ───────────────────")
    accounts = snapshot["accounts"]
    if not accounts:
        lines.append("  (none)")
    else:
        for row in accounts:
            lines.append(f"  {row['platform']}:{row['platform_id']}")
            sub_parts = []
            if row["display_name"] not in (None, ""):
                sub_parts.append(str(row["display_name"]))
            if row["profile_url"] not in (None, ""):
                sub_parts.append(shorten(row["profile_url"], 50))
            if row["source"] not in (None, ""):
                sub_parts.append(f"via {row['source']}")
            if sub_parts:
                lines.append(f"      {' · '.join(sub_parts)}")

    lines.append("")
    lines.append("── reminder ────────────────────────────")
    reminder = snapshot["reminder"]
    if not reminder:
        lines.append("  (none)")
    else:
        due_line = f"  due: {reminder['next_due_at']}"
        if reminder["interval_days"] not in (None, ""):
            due_line += f"  (every {reminder['interval_days']}d)"
        if reminder["note"] not in (None, ""):
            due_line += f"  — {reminder['note']}"
        lines.append(due_line)

    lines.append("")
    lines.append("── recent posts ────────────────────────")
    posts = snapshot["posts"]
    if not posts:
        lines.append("  (none)")
    else:
        for row in posts:
            label = shorten(row["title"] or row["url"], 45)
            plat = f"  [{row['platform']}]" if row["platform"] not in (None, "") else ""
            date = f"  {row['posted_at'][:10]}" if row["posted_at"] not in (None, "") else ""
            lines.append(f"  [{row['id']}] {label}{plat}{date}")

    lines.append("")
    lines.append("── recent work ─────────────────────────")
    work = snapshot["work"]
    if not work:
        lines.append("  (none)")
    else:
        for row in work:
            tags = _fmt_tags(row["tags_json"])
            date = str(row["created_at"])[:10] if row["created_at"] else ""
            suffix_parts = [p for p in [tags, date] if p]
            suffix = "  " + "  ".join(suffix_parts) if suffix_parts else ""
            lines.append(f"  [{row['id']}] {shorten(row['message'], 50)}{suffix}")

    return "\n".join(lines)


def render_post_detail(row: sqlite3.Row) -> str:
    lines = [f"post:{row['id']} for #{row['creator_id']} {row['primary_name']}"]
    for col in ["url", "platform", "platform_post_id", "author_platform_id", "author_name",
                "title", "text", "posted_at", "captured_at", "note", "created_at", "updated_at"]:
        if row[col] not in (None, ""):
            lines.append(f"{col}: {shorten(row[col], 400)}")
    metadata = json_loads(row["metadata_json"], None)
    if metadata is not None:
        lines.append("metadata:")
        lines.append(shorten(json.dumps(metadata, ensure_ascii=False, indent=2), 6000))
    return "\n".join(lines)


def render_work_detail(row: sqlite3.Row) -> str:
    lines = [f"work:{row['id']} for #{row['creator_id']} {row['primary_name']}", f"message: {row['message']}"]
    for label, col in [("tags", "tags_json"), ("paths", "paths_json"), ("urls", "urls_json")]:
        value = json_loads(row[col], [])
        if value:
            lines.append(f"{label}: {', '.join(str(x) for x in value)}")
    if row["created_at"]:
        lines.append(f"created_at: {row['created_at']}")
    if row["updated_at"]:
        lines.append(f"updated_at: {row['updated_at']}")
    metadata = json_loads(row["metadata_json"], None)
    if metadata is not None:
        lines.append("metadata:")
        lines.append(shorten(json.dumps(metadata, ensure_ascii=False, indent=2), 6000))
    return "\n".join(lines)
