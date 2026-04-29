from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .config import LOGGER, app_dir
from .constants import ConflictError
from db import (
    connect,
    delete_profile_if_orphaned,
    get_app_setting,
    profile_row,
    profile_row_by_identity,
    sync_creator_search,
    update_profile,
    update_profile_url,
)
from .gallery import extract_metadata, gallery_available, run_gallery_metadata
from .utils import infer_platform_from_url, now_iso

if TYPE_CHECKING:
    from .controller import Controller


def enrich_pending_profiles(conn: sqlite3.Connection, limit: int) -> int:
    if not gallery_available():
        return 0
    processed = 0
    tasks = conn.execute(
        """
        SELECT j.id,
               j.profile_url_id,
               j.attempts,
               u.url,
               u.profile_id,
               p.creator_id
        FROM profile_enrichment_jobs j
        JOIN profile_urls u ON u.id = j.profile_url_id
        JOIN creator_profiles p ON p.id = u.profile_id
        WHERE j.status = 'pending' AND j.attempts < 3
        ORDER BY j.id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    for task in tasks:
        ts = now_iso()
        conn.execute(
            """
            UPDATE profile_enrichment_jobs
            SET attempts = attempts + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (ts, task["id"]),
        )
        metadata, error = run_gallery_metadata(task["url"], timeout=45, quick=True)
        if error:
            attempts = int(task["attempts"]) + 1
            status = "error" if attempts >= 3 else "pending"
            conn.execute(
                """
                UPDATE profile_enrichment_jobs
                SET status = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, error, now_iso(), task["id"]),
            )
            conn.commit()
            continue

        info = extract_metadata(metadata, task["url"])
        platform = info.get("platform") or infer_platform_from_url(task["url"])
        platform_id = info.get("platform_id")
        display_name = info.get("author_name")

        try:
            with conn:
                current_profile = profile_row(conn, int(task["profile_id"]))
                if current_profile is None:
                    raise ConflictError("Profile disappeared during metadata enrichment")

                if not platform_id:
                    attempts = int(task["attempts"]) + 1
                    status = "error" if attempts >= 3 else "pending"
                    update_profile(
                        conn,
                        int(task["profile_id"]),
                        platform=platform,
                        display_name=display_name,
                        source="gallery-dl",
                        metadata=metadata,
                    )
                    conn.execute(
                        """
                        UPDATE profile_enrichment_jobs
                        SET status = ?,
                            last_error = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            status,
                            "metadata did not resolve platform account",
                            now_iso(),
                            task["id"],
                        ),
                    )
                    sync_creator_search(conn, int(task["creator_id"]))
                    continue

                matched_profile = profile_row_by_identity(conn, platform, platform_id)
                if matched_profile is not None and int(matched_profile["creator_id"]) != int(task["creator_id"]):
                    conn.execute(
                        """
                        UPDATE profile_enrichment_jobs
                        SET status = 'conflict',
                            last_error = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            f"{platform}:{platform_id} belongs to another creator",
                            now_iso(),
                            task["id"],
                        ),
                    )
                    continue

                resolved_profile_id = int(task["profile_id"])
                if matched_profile is not None and int(matched_profile["id"]) != resolved_profile_id:
                    update_profile_url(
                        conn,
                        int(task["profile_url_id"]),
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
                    delete_profile_if_orphaned(conn, resolved_profile_id)
                    resolved_profile_id = int(matched_profile["id"])
                else:
                    update_profile(
                        conn,
                        resolved_profile_id,
                        platform=platform,
                        platform_id=platform_id,
                        display_name=display_name,
                        source="gallery-dl",
                        identity_state="resolved",
                        metadata=metadata,
                    )

                conn.execute(
                    """
                    UPDATE profile_enrichment_jobs
                    SET status = 'done',
                        last_error = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso(), task["id"]),
                )
                sync_creator_search(conn, int(task["creator_id"]))
            processed += 1
        except ConflictError as exc:
            conn.execute(
                """
                UPDATE profile_enrichment_jobs
                SET status = 'conflict',
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (str(exc), now_iso(), task["id"]),
            )
            conn.commit()
    return processed


class SubprocessDriver:
    """CLI / fire-and-forget mode: spawn or run worker as a separate process."""

    @staticmethod
    def spawn(db_path: Path) -> None:
        if os.environ.get("CLOG_NO_WORKER") == "1":
            return
        if not gallery_available():
            return
        conn = connect(db_path)
        try:
            limit = str(int(get_app_setting(conn, "system", "metadata_worker_limit", 5) or 5))
        finally:
            conn.close()
        worker_exe = _resolve_worker_executable()
        if worker_exe is not None:
            command = [str(worker_exe), "--db", str(db_path), "__meta_worker", "--limit", limit]
        elif getattr(sys, "frozen", False):
            command = [sys.executable, "--db", str(db_path), "__meta_worker", "--limit", limit]
        else:
            entry = app_dir() / "clog_cli.py"
            command = [sys.executable, str(entry), "--db", str(db_path), "__meta_worker", "--limit", limit]
        kwargs: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen(command, **kwargs)
        except OSError:
            pass

    @staticmethod
    def process(db_path: Path, limit: int) -> int:
        os.environ["CLOG_NO_WORKER"] = "1"
        if not gallery_available():
            return 0
        conn = connect(db_path)
        try:
            return enrich_pending_profiles(conn, limit)
        finally:
            conn.close()


class InProcessRunner:
    """GUI mode: run enrichment in the controller's job pool, publish a topic when done."""

    def __init__(self, controller: "Controller") -> None:
        self._controller = controller

    def kick(self) -> None:
        self._controller.jobs.submit(self._run_once)

    def _run_once(self) -> None:
        try:
            with self._controller.db() as conn:
                limit = int(get_app_setting(conn, "system", "metadata_worker_limit", 5) or 5)
                processed = enrich_pending_profiles(conn, limit)
        except Exception as exc:
            LOGGER.exception("Enrichment in-process run failed: %s", exc)
            return
        if processed > 0:
            self._controller.pubsub.pub("enrichment.completed", processed=processed)


def _resolve_worker_executable() -> Path | None:
    """When packaged, prefer the CLI exe sibling so we don't accidentally re-launch the GUI."""
    if not getattr(sys, "frozen", False):
        return None
    if os.name != "nt":
        return None
    here = Path(sys.executable).resolve()
    candidate = here.with_name("clog-cli.exe")
    if candidate.exists() and candidate != here:
        return candidate
    return None
