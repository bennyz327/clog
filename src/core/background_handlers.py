from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from db import (
    delete_profile_if_orphaned,
    profile_row,
    profile_row_by_identity,
    profile_url_row_by_id,
    sync_creator_search,
    update_profile,
    update_profile_url,
)
from .background import (
    BackgroundCancelledError,
    BackgroundJobHandler,
    LiveJobStatus,
    RetryableBackgroundError,
)
from .config import LOGGER
from .constants import ConflictError, UserError
from .gallery import extract_metadata, run_gallery_metadata
from .service import (
    add_creator_record,
    add_name_record,
    add_post_record,
    add_url_record,
    refetch_post_meta_record,
)
from .utils import infer_platform_from_url

if TYPE_CHECKING:
    from .background_runtime import BackgroundEnqueueApi


@dataclass
class BackgroundJobContext:
    controller: Any | None
    conn: sqlite3.Connection
    db_path: Path
    worker_id: str
    job_status: LiveJobStatus | None
    enqueue_api: "BackgroundEnqueueApi | None" = None

    def set_status(self, text: str) -> None:
        if self.job_status is not None:
            self.job_status.set_status_text(text)

    def set_progress(self, current: int | None, total: int | None) -> None:
        if self.job_status is not None:
            self.job_status.set_progress(current, total)

    def check_cancelled(self) -> None:
        if self.job_status is not None and self.job_status.cancel_requested():
            raise BackgroundCancelledError("使用者已取消")

    def enqueue_followup(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        source: str = "system",
        dedupe_key: str | None = None,
    ) -> int | None:
        if self.enqueue_api is None:
            return None
        receipt = self.enqueue_api.enqueue(job_type, payload, source=source, dedupe_key=dedupe_key)
        return int(receipt["job_id"])


def _run_profile_resolution(
    conn: sqlite3.Connection,
    profile_url_id: int,
    *,
    live_status: LiveJobStatus | None = None,
    timeout_seconds: int = 45,
) -> dict[str, Any]:
    row = profile_url_row_by_id(conn, int(profile_url_id))
    if row is None:
        raise ConflictError(f"補全中繼資料前，創作者網址 #{profile_url_id} 已不存在")

    url = str(row["url"])
    profile_id = int(row["profile_id"])
    creator_id = int(row["creator_id"])

    if live_status is not None:
        live_status.set_status_text(f"正在補全創作者中繼資料：網址 #{profile_url_id}")

    metadata, error = run_gallery_metadata(
        url,
        timeout=timeout_seconds,
        quick=True,
        live_status=live_status,
    )

    if live_status is not None and live_status.cancel_requested():
        raise BackgroundCancelledError("使用者已取消")

    if error:
        raise RetryableBackgroundError(error)

    info = extract_metadata(metadata, url)
    platform = info.get("platform") or infer_platform_from_url(url)
    platform_id = info.get("platform_id")
    display_name = info.get("author_name")

    current_profile = profile_row(conn, profile_id)
    if current_profile is None:
        raise ConflictError("補全中繼資料期間，創作者資料已不存在")

    if not platform_id:
        update_profile(
            conn,
            profile_id,
            platform=platform,
            display_name=display_name,
            source="gallery-dl",
            metadata=metadata,
        )
        sync_creator_search(conn, creator_id)
        raise RetryableBackgroundError("中繼資料尚未解析出平台帳號")

    matched_profile = profile_row_by_identity(conn, platform, platform_id)
    if matched_profile is not None and int(matched_profile["creator_id"]) != creator_id:
        update_profile(
            conn,
            profile_id,
            platform=platform,
            display_name=display_name,
            source="gallery-dl",
            metadata=metadata,
        )
        sync_creator_search(conn, creator_id)
        return {
            "creator_id": creator_id,
            "profile_id": profile_id,
            "profile_url_id": profile_url_id,
            "data_changed": True,
            "outcome_code": "conflict",
            "message": f"{platform}:{platform_id} 已屬於其他創作者",
        }

    resolved_profile_id = profile_id
    if matched_profile is not None and int(matched_profile["id"]) != profile_id:
        update_profile_url(
            conn,
            profile_url_id,
            profile_id=int(matched_profile["id"]),
            reason="gallery-dl",
        )
        update_profile(
            conn,
            int(matched_profile["id"]),
            display_name=display_name,
            source="gallery-dl",
            identity_state="resolved",
            metadata=metadata,
        )
        delete_profile_if_orphaned(conn, profile_id)
        resolved_profile_id = int(matched_profile["id"])
    else:
        update_profile(
            conn,
            profile_id,
            platform=platform,
            platform_id=platform_id,
            display_name=display_name,
            source="gallery-dl",
            identity_state="resolved",
            metadata=metadata,
        )

    sync_creator_search(conn, creator_id)
    return {
        "creator_id": creator_id,
        "profile_id": resolved_profile_id,
        "profile_url_id": profile_url_id,
        "data_changed": True,
        "outcome_code": "resolved",
    }


def _maybe_follow_up_profile_resolution(
    ctx: BackgroundJobContext,
    result: dict[str, Any],
    *,
    inline_followups: bool,
) -> dict[str, Any]:
    if not result.get("needs_worker"):
        return result

    url_id = result.get("url_id")
    if url_id is None:
        return result

    if inline_followups:
        followup = _run_profile_resolution(
            ctx.conn,
            int(url_id),
            live_status=ctx.job_status,
        )
        followup["queued_followup"] = False
        result["profile_resolution"] = followup
        result["needs_worker"] = False
        result["data_changed"] = True
        return result

    followup_job_id = ctx.enqueue_followup(
        "profile.resolve_metadata",
        {"profile_url_id": int(url_id)},
        source="system",
        dedupe_key=f"profile.resolve_metadata:{int(url_id)}",
    )
    result["followup_job_id"] = followup_job_id
    result["queued_followup"] = True
    return result


def execute_add_creator_with_url(
    ctx: BackgroundJobContext,
    payload: dict[str, Any],
    *,
    inline_followups: bool,
) -> dict[str, Any]:
    result = add_creator_record(
        ctx.conn,
        ctx.db_path,
        str(payload["name"]),
        url=str(payload["url"]),
        note=payload.get("note"),
        live_status=ctx.job_status,
    )
    result["data_changed"] = True
    return _maybe_follow_up_profile_resolution(ctx, result, inline_followups=inline_followups)


def execute_add_name_with_url(
    ctx: BackgroundJobContext,
    payload: dict[str, Any],
    *,
    inline_followups: bool,
) -> dict[str, Any]:
    result = add_name_record(
        ctx.conn,
        ctx.db_path,
        str(payload["target"]),
        str(payload["name"]),
        context=str(payload["context"]),
        live_status=ctx.job_status,
    )
    result["data_changed"] = True
    return _maybe_follow_up_profile_resolution(ctx, result, inline_followups=inline_followups)


def execute_add_url(
    ctx: BackgroundJobContext,
    payload: dict[str, Any],
    *,
    inline_followups: bool,
) -> dict[str, Any]:
    result = add_url_record(
        ctx.conn,
        ctx.db_path,
        str(payload["target"]),
        str(payload["url"]),
        note=payload.get("note"),
        live_status=ctx.job_status,
    )
    result["data_changed"] = True
    return _maybe_follow_up_profile_resolution(ctx, result, inline_followups=inline_followups)


def execute_add_post(ctx: BackgroundJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = add_post_record(
            ctx.conn,
            ctx.db_path,
            str(payload["url"]),
            target=payload.get("target"),
            note=payload.get("note"),
            timeout=int(payload.get("timeout") or 60),
            live_status=ctx.job_status,
        )
    except UserError as exc:
        if ctx.job_status is not None and ctx.job_status.cancel_requested():
            raise BackgroundCancelledError(str(exc)) from exc
        raise
    result["data_changed"] = True
    return result


def execute_refetch_post_meta(ctx: BackgroundJobContext, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = refetch_post_meta_record(
            ctx.conn,
            ctx.db_path,
            int(payload["post_id"]),
            timeout=int(payload.get("timeout") or 60),
            live_status=ctx.job_status,
        )
    except UserError as exc:
        if ctx.job_status is not None and ctx.job_status.cancel_requested():
            raise BackgroundCancelledError(str(exc)) from exc
        raise
    result["data_changed"] = True
    return result


def _run_job(ctx: BackgroundJobContext, job_type: str, payload: dict[str, Any], *, inline_followups: bool) -> dict[str, Any]:
    if job_type == "command.add_creator_with_url":
        return execute_add_creator_with_url(ctx, payload, inline_followups=inline_followups)
    if job_type == "command.add_name_with_url":
        return execute_add_name_with_url(ctx, payload, inline_followups=inline_followups)
    if job_type == "command.add_url":
        return execute_add_url(ctx, payload, inline_followups=inline_followups)
    if job_type == "command.add_post":
        return execute_add_post(ctx, payload)
    if job_type == "command.refetch_post_meta":
        return execute_refetch_post_meta(ctx, payload)
    if job_type == "profile.resolve_metadata":
        return _run_profile_resolution(
            ctx.conn,
            int(payload["profile_url_id"]),
            live_status=ctx.job_status,
        )
    raise KeyError(f"unknown background job type: {job_type}")


def run_background_job(ctx: BackgroundJobContext, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _run_job(ctx, job_type, payload, inline_followups=False)


def run_background_job_inline(
    conn: sqlite3.Connection,
    db_path: Path,
    job_type: str,
    payload: dict[str, Any],
    *,
    live_status: LiveJobStatus | None = None,
) -> dict[str, Any]:
    ctx = BackgroundJobContext(
        controller=None,
        conn=conn,
        db_path=db_path,
        worker_id="inline",
        job_status=live_status,
        enqueue_api=None,
    )
    with conn:
        return _run_job(ctx, job_type, payload, inline_followups=True)


HANDLERS: dict[str, BackgroundJobHandler] = {
    "command.add_creator_with_url": BackgroundJobHandler(
        job_type="command.add_creator_with_url",
        title_builder=lambda payload: f"新增創作者: {payload['name']} ({payload['url']})",
        slot_key="gdl",
        supports_terminate=True,
        max_attempts=1,
        timeout_seconds=60,
    ),
    "command.add_name_with_url": BackgroundJobHandler(
        job_type="command.add_name_with_url",
        title_builder=lambda payload: f"新增別名: {payload['name']} ({payload['context']})",
        slot_key="gdl",
        supports_terminate=True,
        max_attempts=1,
        timeout_seconds=60,
    ),
    "command.add_url": BackgroundJobHandler(
        job_type="command.add_url",
        title_builder=lambda payload: f"新增網址: {payload['url']}",
        slot_key="gdl",
        supports_terminate=True,
        max_attempts=1,
        timeout_seconds=60,
    ),
    "command.add_post": BackgroundJobHandler(
        job_type="command.add_post",
        title_builder=lambda payload: f"新增貼文: {payload['url']}",
        slot_key="gdl",
        supports_terminate=True,
        max_attempts=1,
        timeout_seconds=90,
    ),
    "command.refetch_post_meta": BackgroundJobHandler(
        job_type="command.refetch_post_meta",
        title_builder=lambda payload: f"重新擷取貼文中繼資料：貼文 #{payload['post_id']}",
        slot_key="gdl",
        supports_terminate=True,
        max_attempts=1,
        timeout_seconds=90,
    ),
    "profile.resolve_metadata": BackgroundJobHandler(
        job_type="profile.resolve_metadata",
        title_builder=lambda payload: f"補全創作者中繼資料：網址 #{payload['profile_url_id']}",
        slot_key="gdl",
        supports_terminate=True,
        max_attempts=3,
        timeout_seconds=45,
    ),
}


def get_handler(job_type: str) -> BackgroundJobHandler:
    try:
        return HANDLERS[job_type]
    except KeyError as exc:
        raise KeyError(f"unknown background job type: {job_type}") from exc


def list_job_types() -> list[str]:
    return sorted(HANDLERS)


def describe_payload(job_type: str, payload_json: str) -> str:
    try:
        return get_handler(job_type).title_builder({})
    except Exception:
        return job_type


def log_background_exception(job_type: str, exc: BaseException) -> None:
    LOGGER.exception("Background job %s failed: %s", job_type, exc)
