from __future__ import annotations

import json
import sqlite3
from typing import Any

from db import profile_brief
from .utils import json_loads, shorten


def format_kind_counts(kind_counts: dict[str, int]) -> str:
    order = ["creator", "profile", "profile_url", "alias", "post", "work", "reminder"]
    parts: list[str] = []
    for kind in order:
        count = kind_counts.get(kind)
        if count:
            parts.append(kind if count == 1 else f"{kind} x{count}")
    for kind, count in sorted(kind_counts.items()):
        if kind not in order:
            parts.append(kind if count == 1 else f"{kind} x{count}")
    return ", ".join(parts)


def render_creator_snapshot(snapshot: dict[str, Any]) -> str:
    creator = snapshot["creator"]
    lines = [f"#{creator['id']}  {creator['primary_name']}  [{creator['status']}]"]
    if creator["note"]:
        lines.append(f"  note: {creator['note']}")

    lines.append("")
    lines.append("── aliases ─────────────────────────────")
    aliases = snapshot["aliases"]
    if not aliases:
        lines.append("  (none)")
    else:
        for row in aliases:
            lines.append(f"  [{row['id']}] {row['name']}")
            meta_parts = []
            if row["profile_id"]:
                meta_parts.append(
                    profile_brief(
                        row["platform"],
                        row["platform_id"],
                        row["display_name"],
                    )
                )
            else:
                meta_parts.append("generic")
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
    lines.append("── profiles ────────────────────────────")
    profiles = snapshot["profiles"]
    if not profiles:
        lines.append("  (none)")
    else:
        for row in profiles:
            lines.append(
                f"  [{row['id']}] "
                f"{profile_brief(row['platform'], row['platform_id'], row['display_name'], row['primary_url'])}"
            )
            sub_parts = [row["identity_state"]]
            if row["status"] not in (None, "", "active"):
                sub_parts.append(str(row["status"]))
            if row["source"] not in (None, ""):
                sub_parts.append(f"via {row['source']}")
            if row["note"] not in (None, ""):
                sub_parts.append(f"note: {row['note']}")
            if sub_parts:
                lines.append(f"      {' · '.join(sub_parts)}")
            urls = row["urls"]
            if not urls:
                lines.append("      url: (none)")
            else:
                for url_row in urls:
                    url_parts = [shorten(url_row["url"], 60)]
                    if url_row["reason"] not in (None, ""):
                        url_parts.append(str(url_row["reason"]))
                    if url_row["status"] not in (None, "", "active"):
                        url_parts.append(str(url_row["status"]))
                    if url_row["note"] not in (None, ""):
                        url_parts.append(f"note: {url_row['note']}")
                    lines.append(f"      url: {' · '.join(url_parts)}")

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
            due_line += f"  | {reminder['note']}"
        lines.append(due_line)

    lines.append("")
    lines.append("── recent posts ────────────────────────")
    posts = snapshot["posts"]
    if not posts:
        lines.append("  (none)")
    else:
        for row in posts:
            label = shorten(row["title"] or row["url"], 45)
            profile_text = profile_brief(row["platform"], row["platform_id"], row["display_name"])
            date = f"  {row['posted_at'][:10]}" if row["posted_at"] not in (None, "") else ""
            lines.append(f"  [{row['id']}] {label}  [{profile_text}]{date}")

    lines.append("")
    lines.append("── recent work ─────────────────────────")
    work = snapshot["work"]
    if not work:
        lines.append("  (none)")
    else:
        for row in work:
            date = str(row["created_at"])[:10] if row["created_at"] else ""
            suffix_parts = [part for part in [date] if part]
            suffix = "  " + "  ".join(suffix_parts) if suffix_parts else ""
            lines.append(f"  [{row['id']}] {shorten(row['content'], 50)}{suffix}")

    return "\n".join(lines)


def render_post_detail(row: sqlite3.Row) -> str:
    profile_text = profile_brief(row["platform"], row["platform_id"], row["display_name"])
    lines = [
        f"post:{row['id']} for #{row['creator_id']} {row['primary_name']}",
        f"profile: {profile_text}",
    ]
    for col in ["url", "platform_post_id", "title", "text", "posted_at", "captured_at", "note", "created_at", "updated_at"]:
        if row[col] not in (None, ""):
            lines.append(f"{col}: {shorten(row[col], 400)}")
    metadata = json_loads(row["metadata_json"], None)
    if metadata is not None:
        lines.append("metadata:")
        lines.append(shorten(json.dumps(metadata, ensure_ascii=False, indent=2), 6000))
    return "\n".join(lines)


def render_work_detail(row: sqlite3.Row) -> str:
    lines = [
        f"work:{row['id']} for #{row['creator_id']} {row['primary_name']}",
        "content:",
        str(row["content"] or ""),
    ]
    if row["created_at"]:
        lines.append(f"created_at: {row['created_at']}")
    if row["updated_at"]:
        lines.append(f"updated_at: {row['updated_at']}")
    return "\n".join(lines)
