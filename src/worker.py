from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .config import LOGGER, app_dir, load_config
from .db import (
    connect,
    insert_name_fact,
    insert_url_fact,
    rebuild_search,
    upsert_platform_account,
)
from .constants import UserError
from .gallery import extract_metadata, gallery_available, run_gallery_metadata
from .utils import now_iso


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
    kwargs: dict = {
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
            "SELECT * FROM metadata_tasks WHERE status = 'pending' AND attempts < 3 ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        for task in tasks:
            ts = now_iso()
            conn.execute(
                "UPDATE metadata_tasks SET attempts = attempts + 1, updated_at = ? WHERE id = ?",
                (ts, task["id"]),
            )
            metadata, error = run_gallery_metadata(task["url"], timeout=45, quick=True)
            if error:
                attempts = int(task["attempts"]) + 1
                status = "error" if attempts >= 3 else "pending"
                conn.execute(
                    "UPDATE metadata_tasks SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
                    (status, error, now_iso(), task["id"]),
                )
                conn.commit()
                continue
            info = extract_metadata(metadata, task["url"])
            try:
                with conn:
                    if info.get("platform") and info.get("platform_id"):
                        upsert_platform_account(
                            conn,
                            int(task["creator_id"]),
                            info.get("platform"),
                            info.get("platform_id"),
                            display_name=info.get("author_name"),
                            profile_url=info.get("profile_url") or task["url"],
                            source="gallery-dl",
                            metadata=None,
                        )
                    if info.get("author_name"):
                        insert_name_fact(
                            conn,
                            int(task["creator_id"]),
                            str(info["author_name"]),
                            platform=info.get("platform"),
                            platform_id=info.get("platform_id"),
                            url=info.get("profile_url") or task["url"],
                            reason="metadata",
                            metadata=None,
                        )
                    if info.get("profile_url"):
                        insert_url_fact(
                            conn,
                            int(task["creator_id"]),
                            str(info["profile_url"]),
                            platform=info.get("platform"),
                            platform_id=info.get("platform_id"),
                            name=info.get("author_name"),
                            reason="metadata",
                            metadata=None,
                            schedule_metadata=False,
                        )
                    conn.execute(
                        "UPDATE metadata_tasks SET status = 'done', last_error = NULL, updated_at = ? WHERE id = ?",
                        (now_iso(), task["id"]),
                    )
                    rebuild_search(conn)
                processed += 1
            except UserError as exc:
                conn.execute(
                    "UPDATE metadata_tasks SET status = 'error', last_error = ?, updated_at = ? WHERE id = ?",
                    (str(exc), now_iso(), task["id"]),
                )
                conn.commit()
    finally:
        conn.close()
    return processed
