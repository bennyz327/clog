from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .constants import ConflictError, UserError
from db import (
    creator_label,
    delete_profile_if_orphaned,
    insert_alias,
    insert_creator,
    insert_or_update_post,
    insert_profile,
    insert_profile_url,
    profile_brief,
    profile_row,
    profile_row_by_identity,
    profile_rows_by_creator_platform,
    profile_url_row_by_canonical,
    require_lastrowid,
    resolve_target,
    sync_creator_search,
    touch_creator,
    update_profile,
    update_profile_url,
    upsert_profile_job,
)
from .gallery import classify_url_kind, extract_metadata, run_gallery_metadata
from .utils import canonical_url, infer_platform_from_url, is_url, now_iso


def _profile_lookup_message(platform: str | None, platform_id: str | None) -> str:
    if platform and platform_id:
        return f"{platform}:{platform_id}"
    if platform:
        return platform
    return "the resolved creator account"


def attach_profile_url(
    conn: sqlite3.Connection,
    creator_id: int,
    url: str,
    *,
    note: str | None = None,
    source: str,
    timeout: int = 45,
    create_job: bool = True,
) -> dict[str, Any]:
    canon = canonical_url(url)
    if not canon:
        raise UserError(f"Invalid URL: {url}")

    url_kind = classify_url_kind(url)
    if url_kind == "post-like":
        raise UserError("URL looks like a post URL. Use `clog p` instead.")

    existing_url = profile_url_row_by_canonical(conn, canon)
    if existing_url is not None and int(existing_url["creator_id"]) != creator_id:
        raise ConflictError(
            f"URL is already linked to {creator_label(conn, int(existing_url['creator_id']))}: {url}"
        )

    metadata, error = run_gallery_metadata(url, timeout=timeout, quick=True)
    info = extract_metadata(metadata, url) if metadata else {}
    platform = info.get("platform") or infer_platform_from_url(canon)
    platform_id = info.get("platform_id")
    display_name = info.get("author_name")
    metadata_json = metadata if metadata else None

    profile_id: int
    url_id: int

    if existing_url is not None:
        url_id = int(existing_url["id"])
        current_profile_id = int(existing_url["profile_id"])
        current_profile = profile_row(conn, current_profile_id)
        if current_profile is None:
            raise UserError(f"Profile not found for URL: {url}")

        if platform and platform_id:
            matched_profile = profile_row_by_identity(conn, platform, platform_id)
            if matched_profile is not None and int(matched_profile["creator_id"]) != creator_id:
                raise ConflictError(
                    f"{platform}:{platform_id} is already linked to {creator_label(conn, int(matched_profile['creator_id']))}"
                )
            current_identity = (
                current_profile["platform"],
                current_profile["platform_id"],
            )
            if (
                current_identity[0]
                and current_identity[1]
                and current_identity != (platform, platform_id)
                and matched_profile is None
            ):
                raise ConflictError(
                    f"URL metadata conflicts with existing profile {profile_brief(current_identity[0], current_identity[1], current_profile['display_name'], current_profile['primary_url'])}"
                )
            if matched_profile is not None and int(matched_profile["id"]) != current_profile_id:
                update_profile_url(
                    conn,
                    url_id,
                    profile_id=int(matched_profile["id"]),
                    reason=source,
                    note=note,
                )
                profile_id = int(matched_profile["id"])
                update_profile(
                    conn,
                    profile_id,
                    display_name=display_name,
                    source=source,
                    identity_state="resolved",
                    metadata=metadata_json,
                )
                delete_profile_if_orphaned(conn, current_profile_id)
            else:
                profile_id = current_profile_id
                update_profile(
                    conn,
                    profile_id,
                    platform=platform,
                    platform_id=platform_id,
                    display_name=display_name,
                    source=source,
                    identity_state="resolved",
                    metadata=metadata_json,
                )
        else:
            profile_id = current_profile_id
            update_profile(
                conn,
                profile_id,
                platform=platform,
                display_name=display_name,
                source=source,
                metadata=metadata_json,
            )

        update_profile_url(
            conn,
            url_id,
            reason=source,
            note=note,
        )
    else:
        if platform and platform_id:
            matched_profile = profile_row_by_identity(conn, platform, platform_id)
            if matched_profile is not None:
                if int(matched_profile["creator_id"]) != creator_id:
                    raise ConflictError(
                        f"{platform}:{platform_id} is already linked to {creator_label(conn, int(matched_profile['creator_id']))}"
                    )
                profile_id = int(matched_profile["id"])
                update_profile(
                    conn,
                    profile_id,
                    display_name=display_name,
                    source=source,
                    identity_state="resolved",
                    metadata=metadata_json,
                )
            else:
                profile_id = insert_profile(
                    conn,
                    creator_id,
                    platform=platform,
                    platform_id=platform_id,
                    display_name=display_name,
                    source=source,
                    identity_state="resolved",
                    metadata=metadata_json,
                )
        else:
            profile_id = insert_profile(
                conn,
                creator_id,
                platform=platform,
                display_name=display_name,
                source=source,
                identity_state="unresolved",
                metadata=metadata_json,
            )
        url_id = insert_profile_url(
            conn,
            profile_id,
            url,
            reason=source,
            note=note,
        )

    if create_job and (error or not platform_id):
        upsert_profile_job(conn, url_id)

    return {
        "creator_id": creator_id,
        "profile_id": profile_id,
        "url_id": url_id,
        "warning": error,
        "needs_worker": bool(create_job and (error or not platform_id)),
    }


def add_creator_record(
    conn: sqlite3.Connection,
    _db_path: Path,
    name: str,
    *,
    url: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    with conn:
        creator_id = insert_creator(conn, name, note=note)
        attached: dict[str, Any] | None = None
        if url:
            attached = attach_profile_url(
                conn,
                creator_id,
                url,
                note=note,
                source="initial-url",
            )
        sync_creator_search(conn, creator_id)
    return {
        "creator_id": creator_id,
        "warning": attached["warning"] if attached else None,
        "needs_worker": bool(attached and attached.get("needs_worker")),
    }


def add_name_record(
    conn: sqlite3.Connection,
    _db_path: Path,
    target: str,
    name: str,
    *,
    context: str | None = None,
    profile_id: int | None = None,
) -> dict[str, Any]:
    creator_id = resolve_target(conn, target)
    attached: dict[str, Any] | None = None
    with conn:
        if profile_id is not None:
            profile = profile_row(conn, profile_id)
            if profile is None or int(profile["creator_id"]) != creator_id:
                raise UserError("Selected profile does not belong to the current creator.")
            alias_id = insert_alias(
                conn,
                creator_id,
                name,
                profile_id=profile_id,
                reason="profile-rename",
            )
        elif context and is_url(context):
            attached = attach_profile_url(
                conn,
                creator_id,
                context,
                source="profile-from-url",
            )
            alias_id = insert_alias(
                conn,
                creator_id,
                name,
                profile_id=int(attached["profile_id"]),
                reason="profile-from-url",
            )
        elif context:
            profile_rows = profile_rows_by_creator_platform(conn, creator_id, context)
            if not profile_rows:
                raise UserError(
                    f"No profile exists on {context}. Add the creator URL first."
                )
            if len(profile_rows) > 1:
                raise UserError(
                    f"Multiple profiles exist on {context}. Use a creator URL to disambiguate."
                )
            alias_id = insert_alias(
                conn,
                creator_id,
                name,
                profile_id=int(profile_rows[0]["id"]),
                reason="profile-rename",
            )
            profile_id = int(profile_rows[0]["id"])
        else:
            alias_id = insert_alias(
                conn,
                creator_id,
                name,
                reason="generic-alias",
            )
        sync_creator_search(conn, creator_id)
    return {
        "creator_id": creator_id,
        "profile_id": attached["profile_id"] if attached else profile_id,
        "alias_id": alias_id,
        "warning": attached["warning"] if attached else None,
        "needs_worker": bool(attached and attached.get("needs_worker")),
    }


def add_url_record(
    conn: sqlite3.Connection,
    _db_path: Path,
    target: str,
    url: str,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    creator_id = resolve_target(conn, target)
    with conn:
        attached = attach_profile_url(
            conn,
            creator_id,
            url,
            note=note,
            source="manual-url",
        )
        sync_creator_search(conn, creator_id)
    return attached


def add_post_record(
    conn: sqlite3.Connection,
    _db_path: Path,
    url: str,
    *,
    target: str | None = None,
    note: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    metadata, error = run_gallery_metadata(url, timeout=timeout)
    if error:
        raise UserError(error)
    info = extract_metadata(metadata, url)
    platform = info.get("platform")
    platform_id = info.get("platform_id")
    if not platform or not platform_id:
        raise UserError(
            "Post metadata did not identify a creator account. Add the creator URL first, then retry."
        )
    profile = profile_row_by_identity(conn, platform, platform_id)
    if profile is None:
        raise UserError(
            f"No existing profile matches {_profile_lookup_message(platform, platform_id)}. "
            "Add the creator URL first, then retry."
        )
    creator_id = int(profile["creator_id"])
    if target is not None:
        target_creator_id = resolve_target(conn, target)
        if target_creator_id != creator_id:
            raise ConflictError(
                f"post metadata points to {creator_label(conn, creator_id)}, not {creator_label(conn, target_creator_id)}"
            )

    with conn:
        post_id = insert_or_update_post(
            conn,
            creator_id,
            int(profile["id"]),
            url,
            info,
            note=note,
        )
        sync_creator_search(conn, creator_id)
    return {
        "creator_id": creator_id,
        "profile_id": int(profile["id"]),
        "post_id": post_id,
        "warning": None,
    }


def set_reminder_record(
    conn: sqlite3.Connection,
    target: str,
    when: str,
    *,
    interval_days: int | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    from .utils import parse_when

    creator_id = resolve_target(conn, target)
    due_at, interval_from_when = parse_when(when)
    interval = interval_days if interval_days is not None else interval_from_when
    ts = now_iso()
    with conn:
        existing = conn.execute(
            "SELECT id FROM reminders WHERE creator_id = ?",
            (creator_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE reminders
                SET next_due_at = ?,
                    interval_days = ?,
                    note = coalesce(?, note),
                    updated_at = ?
                WHERE creator_id = ?
                """,
                (due_at, interval, note, ts, creator_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO reminders(
                    creator_id, next_due_at, interval_days, note, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (creator_id, due_at, interval, note, ts, ts),
            )
        touch_creator(conn, creator_id, ts=ts)
        sync_creator_search(conn, creator_id)
    return {"creator_id": creator_id, "due_at": due_at}


def add_work_record(
    conn: sqlite3.Connection,
    target: str,
    content: str,
) -> dict[str, Any]:
    creator_id = resolve_target(conn, target)
    ts = now_iso()
    with conn:
        cur = conn.execute(
            """
            INSERT INTO worklogs(creator_id, content, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            """,
            (
                creator_id,
                content,
                ts,
                ts,
            ),
        )
        touch_creator(conn, creator_id, ts=ts)
        sync_creator_search(conn, creator_id)
    return {"creator_id": creator_id, "work_id": require_lastrowid(cur)}


__all__ = [
    "add_creator_record",
    "add_name_record",
    "add_post_record",
    "add_url_record",
    "add_work_record",
    "attach_profile_url",
    "set_reminder_record",
]
