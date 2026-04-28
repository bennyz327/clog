from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .config import app_dir, load_config
from .constants import ConflictError
from .db import (
    connect,
    delete_profile_if_orphaned,
    profile_row,
    profile_row_by_identity,
    sync_creator_search,
    update_profile,
    update_profile_url,
)
from .gallery import extract_metadata, gallery_available, run_gallery_metadata
from .utils import infer_platform_from_url, now_iso


def maybe_spawn_metadata_worker(db_path: Path) -> None:
    if os.environ.get("CLOG_NO_WORKER") == "1":
        return
    if not gallery_available():
        return
    cfg = load_config()
    limit = str(int(cfg.get("metadata_worker_limit") or 5))
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--db", str(db_path), "__meta_worker", "--limit", limit]
    else:
        entry = app_dir() / "clog.py"
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


def process_metadata_tasks(db_path: Path, limit: int) -> int:
    os.environ["CLOG_NO_WORKER"] = "1"
    if not gallery_available():
        return 0
    conn = connect(db_path)
    processed = 0
    try:
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
    finally:
        conn.close()
    return processed
