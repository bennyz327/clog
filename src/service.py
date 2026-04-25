from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .constants import AmbiguousTarget, ConflictError, UserError
from .db import (
    creator_ids_by_name,
    creator_ids_by_url,
    creator_label,
    insert_creator,
    insert_name_fact,
    insert_or_update_post,
    insert_url_fact,
    platform_account_creator,
    rebuild_search,
    resolve_target,
    upsert_platform_account,
)
from .gallery import extract_metadata, run_gallery_metadata
from .worker import maybe_spawn_metadata_worker


def add_creator_record(
    conn: sqlite3.Connection,
    db_path: Path,
    name: str,
    *,
    url: str | None = None,
    platform: str | None = None,
    platform_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    with conn:
        creator_id = insert_creator(conn, name, note=note)
        if url:
            insert_url_fact(
                conn, creator_id, url,
                platform=platform, platform_id=platform_id,
                name=name, reason="initial", note=note,
            )
        elif platform and platform_id:
            upsert_platform_account(
                conn, creator_id, platform, platform_id,
                display_name=name, source="manual-add",
            )
        rebuild_search(conn)
    if url:
        maybe_spawn_metadata_worker(db_path)
    return {"creator_id": creator_id}


def add_name_record(
    conn: sqlite3.Connection,
    db_path: Path,
    target: str,
    name: str,
    *,
    platform: str | None = None,
    platform_id: str | None = None,
    url: str | None = None,
    from_name: str | None = None,
    reason: str | None = None,
    status: str = "active",
    note: str | None = None,
) -> dict[str, Any]:
    creator_id = resolve_target(conn, target, platform=platform, platform_id=platform_id)
    with conn:
        name_id = insert_name_fact(
            conn, creator_id, name,
            platform=platform, platform_id=platform_id,
            url=url, from_name=from_name, reason=reason, status=status, note=note,
        )
        if platform and platform_id:
            upsert_platform_account(
                conn, creator_id, platform, platform_id,
                display_name=name, profile_url=url, source="manual-name",
            )
        if url:
            insert_url_fact(
                conn, creator_id, url,
                platform=platform, platform_id=platform_id,
                name=name, reason=reason, status=status, note=note,
            )
        rebuild_search(conn)
    if url:
        maybe_spawn_metadata_worker(db_path)
    return {"creator_id": creator_id, "name_id": name_id}


def add_url_record(
    conn: sqlite3.Connection,
    db_path: Path,
    target: str,
    url: str,
    *,
    platform: str | None = None,
    platform_id: str | None = None,
    name: str | None = None,
    from_url: str | None = None,
    reason: str | None = None,
    status: str = "active",
    note: str | None = None,
) -> dict[str, Any]:
    creator_id = resolve_target(conn, target, platform=platform, platform_id=platform_id)
    with conn:
        url_id = insert_url_fact(
            conn, creator_id, url,
            platform=platform, platform_id=platform_id,
            name=name, from_url=from_url, reason=reason, status=status, note=note,
        )
        rebuild_search(conn)
    maybe_spawn_metadata_worker(db_path)
    return {"creator_id": creator_id, "url_id": url_id}


def post_creator_from_metadata(
    conn: sqlite3.Connection,
    url: str,
    metadata_info: dict[str, Any] | None,
    target: str | None,
) -> int:
    metadata_info = metadata_info or {}
    platform = metadata_info.get("platform")
    platform_id = metadata_info.get("platform_id")
    by_account = platform_account_creator(conn, platform, platform_id)
    by_target = resolve_target(conn, target) if target else None
    if by_account is not None and by_target is not None and by_account != by_target:
        raise ConflictError(
            f"metadata account {platform}:{platform_id} points to {creator_label(conn, by_account)}, not {creator_label(conn, by_target)}"
        )
    if by_account is not None:
        return by_account
    if by_target is not None:
        return by_target
    profile_url = metadata_info.get("profile_url")
    if profile_url:
        ids = creator_ids_by_url(conn, str(profile_url))
        if len(ids) == 1:
            return ids[0]
        if len(ids) > 1:
            raise AmbiguousTarget(str(profile_url), ids)
    author_name = metadata_info.get("author_name")
    if author_name:
        ids = creator_ids_by_name(conn, str(author_name))
        if len(ids) == 1:
            return ids[0]
        if len(ids) > 1:
            raise AmbiguousTarget(str(author_name), ids)
    raise UserError("Cannot determine creator. Provide #id, URL, or platform:platform_id as TARGET.")


def add_post_record(
    conn: sqlite3.Connection,
    db_path: Path,
    url: str,
    *,
    target: str | None = None,
    note: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    metadata, error = run_gallery_metadata(url, timeout=timeout)
    info = extract_metadata(metadata, url) if metadata else None
    if error and not target:
        raise UserError(f"{error}. Provide TARGET to store a minimal post.")
    creator_id = post_creator_from_metadata(conn, url, info, target)
    with conn:
        if info and info.get("platform") and info.get("platform_id"):
            upsert_platform_account(
                conn, creator_id, info.get("platform"), info.get("platform_id"),
                display_name=info.get("author_name"), profile_url=info.get("profile_url"),
                source="gallery-dl-post", metadata=metadata,
            )
        if info and info.get("author_name"):
            insert_name_fact(
                conn, creator_id, str(info["author_name"]),
                platform=info.get("platform"), platform_id=info.get("platform_id"),
                url=info.get("profile_url"), reason="post-metadata", metadata=metadata,
            )
        if info and info.get("profile_url"):
            insert_url_fact(
                conn, creator_id, str(info["profile_url"]),
                platform=info.get("platform"), platform_id=info.get("platform_id"),
                name=info.get("author_name"), reason="post-metadata",
                metadata=metadata, schedule_metadata=False,
            )
        post_id = insert_or_update_post(
            conn, creator_id, url, info,
            note=note or (f"metadata unavailable: {error}" if error else None),
        )
        rebuild_search(conn)
    return {"creator_id": creator_id, "post_id": post_id, "warning": error}


def set_reminder_record(
    conn: sqlite3.Connection,
    target: str,
    when: str,
    *,
    interval_days: int | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    from .utils import now_iso, parse_when
    creator_id = resolve_target(conn, target)
    due_at, interval_from_when = parse_when(when)
    interval = interval_days if interval_days is not None else interval_from_when
    ts = now_iso()
    with conn:
        existing = conn.execute(
            "SELECT id FROM reminders WHERE creator_id = ?", (creator_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE reminders SET next_due_at = ?, interval_days = ?, note = coalesce(?, note), updated_at = ? WHERE creator_id = ?",
                (due_at, interval, note, ts, creator_id),
            )
        else:
            conn.execute(
                "INSERT INTO reminders(creator_id, next_due_at, interval_days, note, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?)",
                (creator_id, due_at, interval, note, ts, ts),
            )
        rebuild_search(conn)
    return {"creator_id": creator_id, "due_at": due_at}


def add_work_record(
    conn: sqlite3.Connection,
    target: str,
    message: str,
    *,
    tags: list[str] | None = None,
    paths: list[str] | None = None,
    urls: list[str] | None = None,
    metadata: Any = None,
) -> dict[str, Any]:
    from .utils import json_dumps, now_iso
    creator_id = resolve_target(conn, target)
    ts = now_iso()
    with conn:
        cur = conn.execute(
            """
            INSERT INTO worklogs(
                creator_id, message, tags_json, paths_json, urls_json,
                metadata_json, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (creator_id, message, json_dumps(tags or []), json_dumps(paths or []),
             json_dumps(urls or []), json_dumps(metadata), ts, ts),
        )
        rebuild_search(conn)
    return {"creator_id": creator_id, "work_id": int(cur.lastrowid)}
