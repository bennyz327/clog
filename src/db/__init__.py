from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from core.config import LOGGER, load_legacy_config
from core.constants import AmbiguousTarget, ConflictError, SCHEMA_VERSION, UserError
from core.utils import (
    canonical_url,
    infer_platform_from_url,
    is_url,
    json_dumps,
    json_loads,
    now_epoch,
    normalize_pid,
    normalize_platform,
    now_iso,
    shorten,
    today,
)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    ensure_schema(conn)
    LOGGER.info("DB open %s", db_path)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    version_row = conn.execute(
        "SELECT value FROM app_meta WHERE key = 'schema_version'"
    ).fetchone()
    existing_tables = [
        row["name"]
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
              AND name != 'app_meta'
            """
        )
    ]
    expected_version = str(SCHEMA_VERSION)
    if version_row and str(version_row["value"]) != expected_version:
        raise UserError(
            f"DB schema {version_row['value']} is incompatible with this build. "
            "Delete the development DB file and run `clog init` again."
        )
    if not version_row and existing_tables:
        raise UserError(
            "Existing DB schema is incompatible with this build. "
            "Delete the development DB file and run `clog init` again."
        )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS creators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            primary_name TEXT NOT NULL,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS creator_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
            platform TEXT,
            platform_id TEXT,
            display_name TEXT,
            source TEXT,
            identity_state TEXT NOT NULL DEFAULT 'unresolved',
            status TEXT NOT NULL DEFAULT 'active',
            note TEXT,
            last_metadata_json TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(platform, platform_id)
        );

        CREATE TABLE IF NOT EXISTS profile_urls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES creator_profiles(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL UNIQUE,
            from_url TEXT,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            note TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS creator_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
            profile_id INTEGER REFERENCES creator_profiles(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            note TEXT,
            from_name TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
            profile_id INTEGER NOT NULL REFERENCES creator_profiles(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL UNIQUE,
            platform_post_id TEXT,
            title TEXT,
            text TEXT,
            posted_at TEXT,
            captured_at TEXT NOT NULL,
            metadata_json TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL UNIQUE REFERENCES creators(id) ON DELETE CASCADE,
            next_due_at TEXT NOT NULL,
            interval_days INTEGER,
            note TEXT,
            last_shown_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS worklogs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS background_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            title TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'gui',
            status TEXT NOT NULL DEFAULT 'queued',
            outcome_code TEXT,
            slot_key TEXT NOT NULL DEFAULT 'gdl',
            dedupe_key TEXT,
            priority INTEGER NOT NULL DEFAULT 100,
            payload_json TEXT NOT NULL,
            result_json TEXT,
            error_text TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            run_after INTEGER NOT NULL DEFAULT 0,
            worker_id TEXT,
            lease_expires_at INTEGER,
            heartbeat_at INTEGER,
            cancel_requested_at TEXT,
            terminate_requested_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS background_job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES background_jobs(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS post_meta_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            captured_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'gdl',
            raw_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            scope TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(scope, key)
        );

        CREATE INDEX IF NOT EXISTS idx_creator_aliases_creator ON creator_aliases(creator_id);
        CREATE INDEX IF NOT EXISTS idx_creator_aliases_name ON creator_aliases(name);
        CREATE INDEX IF NOT EXISTS idx_creator_profiles_creator ON creator_profiles(creator_id);
        CREATE INDEX IF NOT EXISTS idx_creator_profiles_platform ON creator_profiles(platform);
        CREATE INDEX IF NOT EXISTS idx_profile_urls_profile ON profile_urls(profile_id);
        CREATE INDEX IF NOT EXISTS idx_posts_creator ON posts(creator_id);
        CREATE INDEX IF NOT EXISTS idx_posts_profile ON posts(profile_id);
        CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(next_due_at);
        CREATE INDEX IF NOT EXISTS idx_worklogs_creator ON worklogs(creator_id);
        CREATE INDEX IF NOT EXISTS idx_background_jobs_status ON background_jobs(status, priority, run_after, id);
        CREATE INDEX IF NOT EXISTS idx_background_jobs_slot ON background_jobs(slot_key, status, run_after, id);
        CREATE INDEX IF NOT EXISTS idx_background_jobs_dedupe ON background_jobs(dedupe_key);
        CREATE INDEX IF NOT EXISTS idx_background_job_events_job ON background_job_events(job_id, id);
        CREATE INDEX IF NOT EXISTS idx_app_events_id ON app_events(id);
        CREATE INDEX IF NOT EXISTS idx_post_meta_history_post ON post_meta_history(post_id, captured_at DESC);

        CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
            kind UNINDEXED,
            row_id UNINDEXED,
            creator_id UNINDEXED,
            title,
            body,
            tokenize = 'unicode61'
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO app_meta(key, value) VALUES('schema_version', ?)",
        (expected_version,),
    )
    _seed_app_settings(conn)
    conn.commit()


def _seed_app_settings(conn: sqlite3.Connection) -> None:
    ts = now_iso()
    existing_count = conn.execute("SELECT count(*) AS c FROM app_settings").fetchone()
    if existing_count is None or int(existing_count["c"]) > 0:
        return

    defaults: dict[tuple[str, str], Any] = {
        ("system", "gallery_dl_command"): None,
        ("system", "background_work_during_idle"): True,
        ("system", "background_work_during_active"): True,
        ("system", "background_idle_threshold_seconds"): 20,
        ("system", "background_queue_poll_interval_ms"): 1000,
        ("system", "background_queue_global_concurrency"): 2,
        ("system", "background_active_packet_ms"): 1000,
        ("system", "background_active_rest_percentage"): 200,
        ("system", "background_idle_packet_ms"): 1000,
        ("system", "background_idle_rest_percentage"): 25,
        ("system", "background_slot_limits"): {
            "gdl": 1,
            "ffmpeg": 1,
            "http": 2,
        },
        ("system", "background_handler_limits"): {
            "profile.resolve_metadata": {"max_attempts": 3, "timeout_seconds": 45},
            "command.add_creator_with_url": {"max_attempts": 1, "timeout_seconds": 60},
            "command.add_name_with_url": {"max_attempts": 1, "timeout_seconds": 60},
            "command.add_url": {"max_attempts": 1, "timeout_seconds": 60},
            "command.add_post": {"max_attempts": 1, "timeout_seconds": 90},
            "command.refetch_post_meta": {"max_attempts": 1, "timeout_seconds": 90},
        },
        ("user", "theme"): "light",
    }

    legacy = load_legacy_config()
    if isinstance(legacy, dict):
        if "gallery_dl_command" in legacy:
            defaults[("system", "gallery_dl_command")] = legacy.get("gallery_dl_command")

    for (scope, key), value in defaults.items():
        conn.execute(
            """
            INSERT INTO app_settings(scope, key, value_json, updated_at)
            VALUES(?, ?, ?, ?)
            """,
            (scope, key, _settings_value_json(value), ts),
        )


def get_app_setting(
    conn: sqlite3.Connection,
    scope: str,
    key: str,
    default: Any = None,
) -> Any:
    row = conn.execute(
        "SELECT value_json FROM app_settings WHERE scope = ? AND key = ?",
        (scope, key),
    ).fetchone()
    if row is None:
        return default
    return json_loads(row["value_json"], default)


def set_app_setting(
    conn: sqlite3.Connection,
    scope: str,
    key: str,
    value: Any,
) -> None:
    conn.execute(
        """
        INSERT INTO app_settings(scope, key, value_json, updated_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(scope, key) DO UPDATE SET
            value_json = excluded.value_json,
            updated_at = excluded.updated_at
        """,
        (scope, key, _settings_value_json(value), now_iso()),
    )


def list_app_settings(conn: sqlite3.Connection, scope: str | None = None) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT scope, key, value_json
        FROM app_settings
        WHERE (? IS NULL OR scope = ?)
        ORDER BY scope, key
        """,
        (scope, scope),
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        scoped = result.setdefault(str(row["scope"]), {})
        scoped[str(row["key"])] = json_loads(row["value_json"], None)
    return result


def read_app_setting(db_path: Path, scope: str, key: str, default: Any = None) -> Any:
    conn = connect(db_path)
    try:
        return get_app_setting(conn, scope, key, default)
    finally:
        conn.close()


def _settings_value_json(value: Any) -> str:
    encoded = json_dumps(value)
    return "null" if encoded is None else encoded


def emit_app_event(conn: sqlite3.Connection, topic: str, payload: Any = None) -> int:
    cur = conn.execute(
        """
        INSERT INTO app_events(topic, payload_json, created_at)
        VALUES(?, ?, ?)
        """,
        (topic, _settings_value_json(payload), now_iso()),
    )
    return require_lastrowid(cur)


def list_app_events_since(conn: sqlite3.Connection, last_event_id: int, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, topic, payload_json, created_at
        FROM app_events
        WHERE id > ?
        ORDER BY id
        LIMIT ?
        """,
        (int(last_event_id), int(limit)),
    ).fetchall()


def get_latest_app_event_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT max(id) AS max_id FROM app_events").fetchone()
    if row is None or row["max_id"] is None:
        return 0
    return int(row["max_id"])


def _insert_background_job_event(
    conn: sqlite3.Connection,
    job_id: int,
    event_type: str,
    payload: Any = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO background_job_events(job_id, event_type, payload_json, created_at)
        VALUES(?, ?, ?, ?)
        """,
        (int(job_id), event_type, _settings_value_json(payload), now_iso()),
    )
    return require_lastrowid(cur)


def enqueue_background_job(
    conn: sqlite3.Connection,
    job_type: str,
    title: str,
    payload: dict[str, Any],
    *,
    source: str = "gui",
    slot_key: str = "gdl",
    dedupe_key: str | None = None,
    priority: int = 100,
    max_attempts: int = 3,
    run_after: int | None = None,
) -> dict[str, Any]:
    ts = now_iso()
    due_at = int(run_after if run_after is not None else now_epoch())
    if dedupe_key:
        existing = conn.execute(
            """
            SELECT id, status
            FROM background_jobs
            WHERE dedupe_key = ?
              AND status IN ('queued', 'running')
            ORDER BY id DESC
            LIMIT 1
            """,
            (dedupe_key,),
        ).fetchone()
        if existing is not None:
            return {
                "job_id": int(existing["id"]),
                "queued": False,
                "deduplicated": True,
            }

    cur = conn.execute(
        """
        INSERT INTO background_jobs(
            job_type, title, source, status, slot_key, dedupe_key, priority,
            payload_json, attempt_count, max_attempts, run_after, created_at, updated_at
        )
        VALUES(?, ?, ?, 'queued', ?, ?, ?, ?, 0, ?, ?, ?, ?)
        """,
        (
            job_type,
            title,
            source,
            slot_key,
            dedupe_key,
            int(priority),
            _settings_value_json(payload),
            int(max_attempts),
            due_at,
            ts,
            ts,
        ),
    )
    job_id = require_lastrowid(cur)
    _insert_background_job_event(conn, job_id, "enqueued", {"source": source})
    emit_app_event(conn, "queue.updated", {"job_id": job_id})
    return {
        "job_id": job_id,
        "queued": True,
        "deduplicated": False,
    }


def list_background_jobs(
    conn: sqlite3.Connection,
    *,
    statuses: tuple[str, ...] | None = None,
    job_type: str | None = None,
    source: str | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)
    if job_type:
        clauses.append("job_type = ?")
        params.append(job_type)
    if source:
        clauses.append("source = ?")
        params.append(source)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    return conn.execute(
        f"""
        SELECT id, job_type, title, source, status, outcome_code, slot_key,
               dedupe_key, priority, payload_json, result_json, error_text,
               attempt_count, max_attempts, run_after, worker_id, lease_expires_at,
               heartbeat_at, cancel_requested_at, terminate_requested_at,
               created_at, updated_at, started_at, finished_at
        FROM background_jobs
        {where}
        ORDER BY
            CASE status
                WHEN 'running' THEN 0
                WHEN 'queued' THEN 1
                ELSE 2
            END,
            priority ASC,
            id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()


def fetch_background_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, job_type, title, source, status, outcome_code, slot_key,
               dedupe_key, priority, payload_json, result_json, error_text,
               attempt_count, max_attempts, run_after, worker_id, lease_expires_at,
               heartbeat_at, cancel_requested_at, terminate_requested_at,
               created_at, updated_at, started_at, finished_at
        FROM background_jobs
        WHERE id = ?
        """,
        (int(job_id),),
    ).fetchone()


def list_background_job_events(conn: sqlite3.Connection, job_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, event_type, payload_json, created_at
        FROM background_job_events
        WHERE job_id = ?
        ORDER BY id
        """,
        (int(job_id),),
    ).fetchall()


def list_runnable_background_jobs(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, job_type, title, source, status, slot_key, priority, payload_json,
               attempt_count, max_attempts, run_after, lease_expires_at,
               cancel_requested_at, terminate_requested_at, created_at, started_at
        FROM background_jobs
        WHERE status = 'queued'
          AND run_after <= ?
        ORDER BY priority ASC, id ASC
        LIMIT ?
        """,
        (now_epoch(), int(limit)),
    ).fetchall()


def claim_background_job(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    worker_id: str,
    lease_seconds: int,
) -> sqlite3.Row | None:
    ts = now_iso()
    now = now_epoch()
    lease_expires = now + int(lease_seconds)
    cur = conn.execute(
        """
        UPDATE background_jobs
        SET status = 'running',
            worker_id = ?,
            lease_expires_at = ?,
            heartbeat_at = ?,
            started_at = coalesce(started_at, ?),
            updated_at = ?,
            attempt_count = attempt_count + 1
        WHERE id = ?
          AND status = 'queued'
        """,
        (worker_id, lease_expires, now, ts, ts, int(job_id)),
    )
    if cur.rowcount == 0:
        return None
    _insert_background_job_event(conn, job_id, "running", {"worker_id": worker_id})
    emit_app_event(conn, "queue.updated", {"job_id": int(job_id)})
    return fetch_background_job(conn, int(job_id))


def heartbeat_background_job(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    worker_id: str,
    lease_seconds: int,
) -> bool:
    cur = conn.execute(
        """
        UPDATE background_jobs
        SET heartbeat_at = ?,
            lease_expires_at = ?,
            updated_at = ?
        WHERE id = ?
          AND status = 'running'
          AND worker_id = ?
        """,
        (now_epoch(), now_epoch() + int(lease_seconds), now_iso(), int(job_id), worker_id),
    )
    return cur.rowcount > 0


def complete_background_job(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    result: Any = None,
    outcome_code: str | None = None,
) -> None:
    ts = now_iso()
    conn.execute(
        """
        UPDATE background_jobs
        SET status = 'succeeded',
            outcome_code = ?,
            result_json = ?,
            error_text = NULL,
            worker_id = NULL,
            lease_expires_at = NULL,
            heartbeat_at = NULL,
            updated_at = ?,
            finished_at = ?
        WHERE id = ?
        """,
        (outcome_code, _settings_value_json(result), ts, ts, int(job_id)),
    )
    _insert_background_job_event(conn, job_id, "succeeded", {"outcome_code": outcome_code})
    emit_app_event(conn, "queue.updated", {"job_id": int(job_id)})


def requeue_background_job_after_retryable_error(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    error_text: str,
    delay_seconds: int = 30,
) -> None:
    ts = now_iso()
    conn.execute(
        """
        UPDATE background_jobs
        SET status = 'queued',
            error_text = ?,
            worker_id = NULL,
            lease_expires_at = NULL,
            heartbeat_at = NULL,
            updated_at = ?,
            run_after = ?
        WHERE id = ?
        """,
        (error_text, ts, now_epoch() + int(delay_seconds), int(job_id)),
    )
    _insert_background_job_event(conn, job_id, "requeued", {"error": error_text, "delay_seconds": delay_seconds})
    emit_app_event(conn, "queue.updated", {"job_id": int(job_id)})


def fail_background_job(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    error_text: str,
    outcome_code: str | None = None,
) -> None:
    ts = now_iso()
    conn.execute(
        """
        UPDATE background_jobs
        SET status = 'failed',
            outcome_code = ?,
            error_text = ?,
            worker_id = NULL,
            lease_expires_at = NULL,
            heartbeat_at = NULL,
            updated_at = ?,
            finished_at = ?
        WHERE id = ?
        """,
        (outcome_code, error_text, ts, ts, int(job_id)),
    )
    _insert_background_job_event(conn, job_id, "failed", {"error": error_text, "outcome_code": outcome_code})
    emit_app_event(conn, "queue.updated", {"job_id": int(job_id)})


def cancel_queued_background_job(conn: sqlite3.Connection, job_id: int) -> bool:
    ts = now_iso()
    cur = conn.execute(
        """
        UPDATE background_jobs
        SET status = 'cancelled',
            error_text = 'cancelled before start',
            updated_at = ?,
            finished_at = ?
        WHERE id = ?
          AND status = 'queued'
        """,
        (ts, ts, int(job_id)),
    )
    if cur.rowcount == 0:
        return False
    _insert_background_job_event(conn, job_id, "cancelled", {"reason": "cancelled before start"})
    emit_app_event(conn, "queue.updated", {"job_id": int(job_id)})
    return True


def cancel_running_background_job(conn: sqlite3.Connection, job_id: int) -> bool:
    ts = now_iso()
    cur = conn.execute(
        """
        UPDATE background_jobs
        SET cancel_requested_at = ?,
            updated_at = ?
        WHERE id = ?
          AND status = 'running'
        """,
        (ts, ts, int(job_id)),
    )
    if cur.rowcount == 0:
        return False
    _insert_background_job_event(conn, job_id, "cancel_requested", None)
    emit_app_event(conn, "queue.updated", {"job_id": int(job_id)})
    return True


def request_terminate_background_job(conn: sqlite3.Connection, job_id: int) -> bool:
    ts = now_iso()
    cur = conn.execute(
        """
        UPDATE background_jobs
        SET cancel_requested_at = coalesce(cancel_requested_at, ?),
            terminate_requested_at = ?,
            updated_at = ?
        WHERE id = ?
          AND status = 'running'
        """,
        (ts, ts, ts, int(job_id)),
    )
    if cur.rowcount == 0:
        return False
    _insert_background_job_event(conn, job_id, "terminate_requested", None)
    emit_app_event(conn, "queue.updated", {"job_id": int(job_id)})
    return True


def finish_cancelled_background_job(conn: sqlite3.Connection, job_id: int, *, error_text: str) -> None:
    ts = now_iso()
    conn.execute(
        """
        UPDATE background_jobs
        SET status = 'cancelled',
            error_text = ?,
            worker_id = NULL,
            lease_expires_at = NULL,
            heartbeat_at = NULL,
            updated_at = ?,
            finished_at = ?
        WHERE id = ?
        """,
        (error_text, ts, ts, int(job_id)),
    )
    _insert_background_job_event(conn, job_id, "cancelled", {"reason": error_text})
    emit_app_event(conn, "queue.updated", {"job_id": int(job_id)})


def retry_background_job(conn: sqlite3.Connection, job_id: int) -> bool:
    ts = now_iso()
    cur = conn.execute(
        """
        UPDATE background_jobs
        SET status = 'queued',
            outcome_code = NULL,
            result_json = NULL,
            error_text = NULL,
            attempt_count = 0,
            run_after = ?,
            worker_id = NULL,
            lease_expires_at = NULL,
            heartbeat_at = NULL,
            cancel_requested_at = NULL,
            terminate_requested_at = NULL,
            started_at = NULL,
            finished_at = NULL,
            updated_at = ?
        WHERE id = ?
          AND status IN ('failed', 'cancelled', 'succeeded')
        """,
        (now_epoch(), ts, int(job_id)),
    )
    if cur.rowcount == 0:
        return False
    _insert_background_job_event(conn, job_id, "retry_requested", None)
    emit_app_event(conn, "queue.updated", {"job_id": int(job_id)})
    return True


def purge_background_jobs(
    conn: sqlite3.Connection,
    *,
    job_ids: list[int] | None = None,
    statuses: tuple[str, ...] = ("succeeded", "failed", "cancelled"),
) -> int:
    if job_ids:
        placeholders = ", ".join("?" for _ in job_ids)
        cur = conn.execute(
            f"""
            DELETE FROM background_jobs
            WHERE id IN ({placeholders})
              AND status IN ('succeeded', 'failed', 'cancelled')
            """,
            tuple(int(job_id) for job_id in job_ids),
        )
        if cur.rowcount:
            emit_app_event(conn, "queue.updated", {"purged": int(cur.rowcount)})
        return int(cur.rowcount or 0)

    placeholders = ", ".join("?" for _ in statuses)
    cur = conn.execute(
        f"DELETE FROM background_jobs WHERE status IN ({placeholders})",
        statuses,
    )
    if cur.rowcount:
        emit_app_event(conn, "queue.updated", {"purged": int(cur.rowcount)})
    return int(cur.rowcount or 0)


def recover_stale_background_jobs(conn: sqlite3.Connection) -> int:
    ts = now_iso()
    cur = conn.execute(
        """
        UPDATE background_jobs
        SET status = 'queued',
            worker_id = NULL,
            lease_expires_at = NULL,
            heartbeat_at = NULL,
            updated_at = ?
        WHERE status = 'running'
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at < ?
        """,
        (ts, now_epoch()),
    )
    recovered = int(cur.rowcount or 0)
    if recovered:
        conn.execute(
            """
            INSERT INTO background_job_events(job_id, event_type, payload_json, created_at)
            SELECT id, 'recovered_stale', NULL, ?
            FROM background_jobs
            WHERE status = 'queued'
              AND updated_at = ?
            """,
            (ts, ts),
        )
        emit_app_event(conn, "queue.updated", {"recovered": recovered})
    return recovered


def require_lastrowid(cur: sqlite3.Cursor) -> int:
    rowid = cur.lastrowid
    if rowid is None:
        raise RuntimeError("insert did not produce lastrowid")
    return int(rowid)


def creator_exists(conn: sqlite3.Connection, creator_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM creators WHERE id = ?", (creator_id,)).fetchone()
    return row is not None


def creator_label(conn: sqlite3.Connection, creator_id: int) -> str:
    row = conn.execute(
        "SELECT id, primary_name FROM creators WHERE id = ?", (creator_id,)
    ).fetchone()
    if not row:
        return f"#{creator_id}"
    return f"#{row['id']} {row['primary_name']}"


def touch_creator(conn: sqlite3.Connection, creator_id: int, *, ts: str | None = None) -> None:
    conn.execute(
        "UPDATE creators SET updated_at = ? WHERE id = ?",
        (ts or now_iso(), creator_id),
    )


def profile_identity_text(platform: str | None, platform_id: str | None) -> str:
    if platform and platform_id:
        return f"{platform}:{platform_id}"
    if platform:
        return platform
    return "unresolved"


def profile_brief(
    platform: str | None,
    platform_id: str | None,
    display_name: str | None = None,
    url: str | None = None,
) -> str:
    parts = [profile_identity_text(platform, platform_id)]
    if display_name:
        parts.append(display_name)
    if url:
        parts.append(shorten(url, 60))
    return " | ".join(parts)


def profile_row(conn: sqlite3.Connection, profile_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT p.*,
               c.primary_name,
               (
                   SELECT u.url
                   FROM profile_urls u
                   WHERE u.profile_id = p.id
                   ORDER BY u.updated_at DESC, u.id DESC
                   LIMIT 1
               ) AS primary_url
        FROM creator_profiles p
        JOIN creators c ON c.id = p.creator_id
        WHERE p.id = ?
        """,
        (profile_id,),
    ).fetchone()


def profile_row_by_identity(
    conn: sqlite3.Connection,
    platform: str | None,
    platform_id: str | None,
) -> sqlite3.Row | None:
    platform = normalize_platform(platform)
    platform_id = normalize_pid(platform_id)
    if not platform or not platform_id:
        return None
    return conn.execute(
        """
        SELECT p.*,
               (
                   SELECT u.url
                   FROM profile_urls u
                   WHERE u.profile_id = p.id
                   ORDER BY u.updated_at DESC, u.id DESC
                   LIMIT 1
               ) AS primary_url
        FROM creator_profiles p
        WHERE p.platform = ? AND p.platform_id = ?
        """,
        (platform, platform_id),
    ).fetchone()


def profile_rows_by_creator(
    conn: sqlite3.Connection,
    creator_id: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.*,
               (
                   SELECT u.url
                   FROM profile_urls u
                   WHERE u.profile_id = p.id
                   ORDER BY u.updated_at DESC, u.id DESC
                   LIMIT 1
               ) AS primary_url
        FROM creator_profiles p
        WHERE p.creator_id = ?
        ORDER BY p.updated_at DESC, p.id DESC
        """,
        (creator_id,),
    ).fetchall()


def profile_rows_by_creator_platform(
    conn: sqlite3.Connection,
    creator_id: int,
    platform: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.*,
               (
                   SELECT u.url
                   FROM profile_urls u
                   WHERE u.profile_id = p.id
                   ORDER BY u.updated_at DESC, u.id DESC
                   LIMIT 1
               ) AS primary_url
        FROM creator_profiles p
        WHERE p.creator_id = ? AND p.platform = ?
        ORDER BY p.updated_at DESC, p.id DESC
        """,
        (creator_id, normalize_platform(platform)),
    ).fetchall()


def profile_url_row_by_canonical(
    conn: sqlite3.Connection,
    canonical: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT u.*,
               p.creator_id,
               p.platform,
               p.platform_id,
               p.display_name,
               p.identity_state,
               c.primary_name
        FROM profile_urls u
        JOIN creator_profiles p ON p.id = u.profile_id
        JOIN creators c ON c.id = p.creator_id
        WHERE u.canonical_url = ?
        """,
        (canonical,),
    ).fetchone()


def profile_url_row_by_id(
    conn: sqlite3.Connection,
    profile_url_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT u.*,
               p.creator_id,
               p.platform,
               p.platform_id,
               p.display_name,
               p.identity_state,
               c.primary_name
        FROM profile_urls u
        JOIN creator_profiles p ON p.id = u.profile_id
        JOIN creators c ON c.id = p.creator_id
        WHERE u.id = ?
        """,
        (int(profile_url_id),),
    ).fetchone()


def profile_url_rows_for_profile(
    conn: sqlite3.Connection,
    profile_id: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM profile_urls
        WHERE profile_id = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (profile_id,),
    ).fetchall()


def creator_ids_by_url(conn: sqlite3.Connection, value: str) -> list[int]:
    canon = canonical_url(value)
    if not canon:
        return []
    row = profile_url_row_by_canonical(conn, canon)
    if row is None:
        return []
    return [int(row["creator_id"])]


def creator_ids_by_name(conn: sqlite3.Connection, value: str) -> list[int]:
    q = value.strip()
    if not q:
        return []
    exact: set[int] = set()
    for row in conn.execute(
        "SELECT id FROM creators WHERE lower(primary_name) = lower(?)",
        (q,),
    ):
        exact.add(int(row["id"]))
    for row in conn.execute(
        "SELECT creator_id FROM creator_aliases WHERE lower(name) = lower(?)",
        (q,),
    ):
        exact.add(int(row["creator_id"]))
    if exact:
        return sorted(exact)

    like = f"%{q}%"
    fuzzy: set[int] = set()
    for row in conn.execute(
        "SELECT id FROM creators WHERE primary_name LIKE ? LIMIT 20",
        (like,),
    ):
        fuzzy.add(int(row["id"]))
    for row in conn.execute(
        "SELECT creator_id FROM creator_aliases WHERE name LIKE ? LIMIT 20",
        (like,),
    ):
        fuzzy.add(int(row["creator_id"]))
    return sorted(fuzzy)


def parse_platform_target(value: str) -> tuple[str, str] | None:
    if "://" in value or value.startswith("#"):
        return None
    if ":" not in value:
        return None
    platform, platform_id = value.split(":", 1)
    platform = normalize_platform(platform)
    platform_id = normalize_pid(platform_id)
    if not platform or not platform_id:
        return None
    return platform, platform_id


def resolve_target(conn: sqlite3.Connection, target: str) -> int:
    target = target.strip()
    if not target:
        raise UserError("Target is empty")

    if target.startswith("#"):
        try:
            creator_id = int(target[1:])
        except ValueError as exc:
            raise UserError(f"Invalid creator id: {target}") from exc
        if not creator_exists(conn, creator_id):
            raise UserError(f"Creator not found: {target}")
        return creator_id

    if is_url(target):
        ids = creator_ids_by_url(conn, target)
        if len(ids) == 1:
            return ids[0]
        if len(ids) > 1:
            raise AmbiguousTarget(target, ids)
        raise UserError(f"No creator is linked to URL: {target}")

    parsed = parse_platform_target(target)
    if parsed:
        row = profile_row_by_identity(conn, parsed[0], parsed[1])
        if row is None:
            raise UserError(f"No creator is linked to platform id: {target}")
        return int(row["creator_id"])

    ids = creator_ids_by_name(conn, target)
    if len(ids) == 1:
        return ids[0]
    if len(ids) > 1:
        raise AmbiguousTarget(target, ids)
    raise UserError(f"Creator not found: {target}")


def candidate_lines(conn: sqlite3.Connection, creator_ids: list[int]) -> list[str]:
    lines: list[str] = []
    for cid in creator_ids[:10]:
        creator = conn.execute(
            "SELECT id, primary_name FROM creators WHERE id = ?",
            (cid,),
        ).fetchone()
        if creator is None:
            continue
        names = [
            row["name"]
            for row in conn.execute(
                """
                SELECT DISTINCT name
                FROM creator_aliases
                WHERE creator_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 4
                """,
                (cid,),
            )
        ]
        profiles = [
            profile_brief(
                row["platform"],
                row["platform_id"],
                row["display_name"],
                row["primary_url"],
            )
            for row in profile_rows_by_creator(conn, cid)[:3]
        ]
        urls = [
            shorten(row["url"], 60)
            for row in conn.execute(
                """
                SELECT u.url
                FROM profile_urls u
                JOIN creator_profiles p ON p.id = u.profile_id
                WHERE p.creator_id = ?
                ORDER BY u.updated_at DESC, u.id DESC
                LIMIT 2
                """,
                (cid,),
            )
        ]
        detail = " | ".join(
            part
            for part in [", ".join(names), "; ".join(profiles), ", ".join(urls)]
            if part
        )
        lines.append(
            f"  #{creator['id']} {creator['primary_name']}"
            + (f" ({detail})" if detail else "")
        )
    if len(creator_ids) > 10:
        lines.append(f"  ... and {len(creator_ids) - 10} more")
    return lines


def insert_creator(conn: sqlite3.Connection, name: str, note: str | None = None) -> int:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO creators(primary_name, note, created_at, updated_at) VALUES(?, ?, ?, ?)",
        (name, note, ts, ts),
    )
    creator_id = require_lastrowid(cur)
    insert_alias(
        conn,
        creator_id,
        name,
        reason="initial",
        note=note,
    )
    return creator_id


def insert_alias(
    conn: sqlite3.Connection,
    creator_id: int,
    name: str,
    *,
    profile_id: int | None = None,
    reason: str,
    status: str = "active",
    note: str | None = None,
    from_name: str | None = None,
) -> int:
    ts = now_iso()
    existing = conn.execute(
        """
        SELECT id
        FROM creator_aliases
        WHERE creator_id = ?
          AND coalesce(profile_id, 0) = coalesce(?, 0)
          AND lower(name) = lower(?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (creator_id, profile_id, name),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE creator_aliases
            SET reason = coalesce(?, reason),
                status = coalesce(?, status),
                note = coalesce(?, note),
                from_name = coalesce(?, from_name),
                last_seen_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (reason, status, note, from_name, ts, ts, existing["id"]),
        )
        touch_creator(conn, creator_id, ts=ts)
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO creator_aliases(
            creator_id, profile_id, name, reason, status, note, from_name,
            first_seen_at, last_seen_at, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (creator_id, profile_id, name, reason, status, note, from_name, ts, ts, ts, ts),
    )
    touch_creator(conn, creator_id, ts=ts)
    return require_lastrowid(cur)


def insert_profile(
    conn: sqlite3.Connection,
    creator_id: int,
    *,
    platform: str | None = None,
    platform_id: str | None = None,
    display_name: str | None = None,
    source: str | None = None,
    identity_state: str = "unresolved",
    status: str = "active",
    note: str | None = None,
    metadata: Any = None,
) -> int:
    ts = now_iso()
    cur = conn.execute(
        """
        INSERT INTO creator_profiles(
            creator_id, platform, platform_id, display_name, source,
            identity_state, status, note, last_metadata_json,
            first_seen_at, last_seen_at, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            creator_id,
            normalize_platform(platform),
            normalize_pid(platform_id),
            display_name,
            source,
            identity_state,
            status,
            note,
            json_dumps(metadata),
            ts,
            ts,
            ts,
            ts,
        ),
    )
    touch_creator(conn, creator_id, ts=ts)
    return require_lastrowid(cur)


def update_profile(
    conn: sqlite3.Connection,
    profile_id: int,
    *,
    platform: str | None = None,
    platform_id: str | None = None,
    display_name: str | None = None,
    source: str | None = None,
    identity_state: str | None = None,
    status: str | None = None,
    note: str | None = None,
    metadata: Any = None,
) -> None:
    row = profile_row(conn, profile_id)
    if row is None:
        raise UserError(f"Profile not found: {profile_id}")
    platform = normalize_platform(platform)
    platform_id = normalize_pid(platform_id)
    if platform and platform_id:
        conflict = profile_row_by_identity(conn, platform, platform_id)
        if conflict is not None and int(conflict["id"]) != profile_id:
            raise ConflictError(
                f"{platform}:{platform_id} is already linked to {creator_label(conn, int(conflict['creator_id']))}"
            )
    ts = now_iso()
    conn.execute(
        """
        UPDATE creator_profiles
        SET platform = coalesce(?, platform),
            platform_id = coalesce(?, platform_id),
            display_name = coalesce(?, display_name),
            source = coalesce(?, source),
            identity_state = coalesce(?, identity_state),
            status = coalesce(?, status),
            note = coalesce(?, note),
            last_metadata_json = coalesce(?, last_metadata_json),
            last_seen_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            platform,
            platform_id,
            display_name,
            source,
            identity_state,
            status,
            note,
            json_dumps(metadata),
            ts,
            ts,
            profile_id,
        ),
    )
    touch_creator(conn, int(row["creator_id"]), ts=ts)


def insert_profile_url(
    conn: sqlite3.Connection,
    profile_id: int,
    url: str,
    *,
    from_url: str | None = None,
    reason: str | None = None,
    status: str = "active",
    note: str | None = None,
) -> int:
    canon = canonical_url(url)
    if not canon:
        raise UserError(f"Invalid URL: {url}")
    ts = now_iso()
    cur = conn.execute(
        """
        INSERT INTO profile_urls(
            profile_id, url, canonical_url, from_url, reason,
            status, note, first_seen_at, last_seen_at, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (profile_id, url, canon, from_url, reason, status, note, ts, ts, ts, ts),
    )
    profile = profile_row(conn, profile_id)
    if profile is not None:
        touch_creator(conn, int(profile["creator_id"]), ts=ts)
    return require_lastrowid(cur)


def update_profile_url(
    conn: sqlite3.Connection,
    url_id: int,
    *,
    profile_id: int | None = None,
    from_url: str | None = None,
    reason: str | None = None,
    status: str | None = None,
    note: str | None = None,
) -> None:
    row = conn.execute(
        """
        SELECT u.*, p.creator_id
        FROM profile_urls u
        JOIN creator_profiles p ON p.id = u.profile_id
        WHERE u.id = ?
        """,
        (url_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"Profile URL not found: {url_id}")
    ts = now_iso()
    conn.execute(
        """
        UPDATE profile_urls
        SET profile_id = coalesce(?, profile_id),
            from_url = coalesce(?, from_url),
            reason = coalesce(?, reason),
            status = coalesce(?, status),
            note = coalesce(?, note),
            last_seen_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (profile_id, from_url, reason, status, note, ts, ts, url_id),
    )
    touch_creator(conn, int(row["creator_id"]), ts=ts)
    if profile_id is not None and profile_id != int(row["profile_id"]):
        profile = profile_row(conn, profile_id)
        if profile is not None:
            touch_creator(conn, int(profile["creator_id"]), ts=ts)


def delete_profile_if_orphaned(conn: sqlite3.Connection, profile_id: int) -> None:
    row = profile_row(conn, profile_id)
    if row is None:
        return
    counts = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM profile_urls WHERE profile_id = ?) AS url_count,
            (SELECT count(*) FROM creator_aliases WHERE profile_id = ?) AS alias_count,
            (SELECT count(*) FROM posts WHERE profile_id = ?) AS post_count
        """,
        (profile_id, profile_id, profile_id),
    ).fetchone()
    if counts is None:
        return
    if int(counts["url_count"]) == 0 and int(counts["alias_count"]) == 0 and int(counts["post_count"]) == 0:
        conn.execute("DELETE FROM creator_profiles WHERE id = ?", (profile_id,))
        touch_creator(conn, int(row["creator_id"]))


def insert_or_update_post(
    conn: sqlite3.Connection,
    creator_id: int,
    profile_id: int,
    url: str,
    metadata_info: dict[str, Any] | None,
    *,
    note: str | None = None,
) -> int:
    canon = canonical_url(url)
    if not canon:
        raise UserError(f"Invalid URL: {url}")
    ts = now_iso()
    info = metadata_info or {}
    existing = conn.execute(
        "SELECT id, creator_id, profile_id FROM posts WHERE canonical_url = ?",
        (canon,),
    ).fetchone()
    if existing and int(existing["creator_id"]) != creator_id:
        raise ConflictError(
            f"Post URL is already linked to {creator_label(conn, int(existing['creator_id']))}: {url}"
        )
    if existing and int(existing["profile_id"]) != profile_id:
        raise ConflictError(
            f"Post URL is already linked to a different profile for {creator_label(conn, creator_id)}: {url}"
        )
    if existing:
        conn.execute(
            """
            UPDATE posts
            SET platform_post_id = coalesce(?, platform_post_id),
                title = coalesce(?, title),
                text = coalesce(?, text),
                posted_at = coalesce(?, posted_at),
                metadata_json = coalesce(?, metadata_json),
                note = coalesce(?, note),
                captured_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                info.get("post_id"),
                info.get("title"),
                info.get("text"),
                info.get("posted_at"),
                json_dumps(info.get("raw")),
                note,
                ts,
                ts,
                existing["id"],
            ),
        )
        touch_creator(conn, creator_id, ts=ts)
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO posts(
            creator_id, profile_id, url, canonical_url, platform_post_id,
            title, text, posted_at, captured_at, metadata_json, note,
            created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            creator_id,
            profile_id,
            url,
            canon,
            info.get("post_id"),
            info.get("title"),
            info.get("text"),
            info.get("posted_at"),
            ts,
            json_dumps(info.get("raw")),
            note,
            ts,
            ts,
        ),
    )
    touch_creator(conn, creator_id, ts=ts)
    return require_lastrowid(cur)


def scalar_texts(value: Any, limit: int = 8000) -> str:
    texts: list[str] = []

    def walk(item: Any) -> None:
        if sum(len(text) for text in texts) > limit:
            return
        if item is None:
            return
        if isinstance(item, (str, int, float, bool)):
            texts.append(str(item))
            return
        if isinstance(item, dict):
            for key, val in item.items():
                if isinstance(key, str):
                    texts.append(key)
                walk(val)
            return
        if isinstance(item, list):
            for val in item:
                walk(val)

    walk(value)
    return " ".join(texts)[:limit]


def _search_add(
    conn: sqlite3.Connection,
    kind: str,
    row_id: int,
    creator_id: int,
    title: str,
    body: str,
) -> None:
    conn.execute(
        "INSERT INTO search_fts(kind, row_id, creator_id, title, body) VALUES(?, ?, ?, ?, ?)",
        (kind, row_id, creator_id, title, body),
    )


def sync_creator_search(conn: sqlite3.Connection, creator_id: int) -> None:
    conn.execute("DELETE FROM search_fts WHERE creator_id = ?", (creator_id,))
    creator = conn.execute(
        "SELECT * FROM creators WHERE id = ?",
        (creator_id,),
    ).fetchone()
    if creator is None:
        return
    _search_add(
        conn,
        "creator",
        int(creator["id"]),
        creator_id,
        creator["primary_name"],
        " ".join([creator["primary_name"], creator["note"] or "", creator["status"] or ""]),
    )

    for row in conn.execute(
        """
        SELECT a.*, p.platform, p.platform_id, p.display_name
        FROM creator_aliases a
        LEFT JOIN creator_profiles p ON p.id = a.profile_id
        WHERE a.creator_id = ?
        ORDER BY a.id
        """,
        (creator_id,),
    ):
        _search_add(
            conn,
            "alias",
            int(row["id"]),
            creator_id,
            row["name"],
            " ".join(
                str(item or "")
                for item in [
                    row["name"],
                    row["platform"],
                    row["platform_id"],
                    row["display_name"],
                    row["from_name"],
                    row["reason"],
                    row["status"],
                    row["note"],
                ]
            ),
        )

    for row in conn.execute(
        "SELECT * FROM creator_profiles WHERE creator_id = ? ORDER BY id",
        (creator_id,),
    ):
        _search_add(
            conn,
            "profile",
            int(row["id"]),
            creator_id,
            profile_identity_text(row["platform"], row["platform_id"]),
            " ".join(
                str(item or "")
                for item in [
                    row["platform"],
                    row["platform_id"],
                    row["display_name"],
                    row["source"],
                    row["identity_state"],
                    row["status"],
                    row["note"],
                    scalar_texts(json_loads(row["last_metadata_json"], {}), 2000),
                ]
            ),
        )

    for row in conn.execute(
        """
        SELECT u.*
        FROM profile_urls u
        JOIN creator_profiles p ON p.id = u.profile_id
        WHERE p.creator_id = ?
        ORDER BY u.id
        """,
        (creator_id,),
    ):
        _search_add(
            conn,
            "profile_url",
            int(row["id"]),
            creator_id,
            row["url"],
            " ".join(
                str(item or "")
                for item in [
                    row["url"],
                    row["from_url"],
                    row["reason"],
                    row["status"],
                    row["note"],
                ]
            ),
        )

    for row in conn.execute(
        "SELECT * FROM posts WHERE creator_id = ? ORDER BY id",
        (creator_id,),
    ):
        _search_add(
            conn,
            "post",
            int(row["id"]),
            creator_id,
            row["title"] or row["url"],
            " ".join(
                str(item or "")
                for item in [
                    row["url"],
                    row["platform_post_id"],
                    row["title"],
                    row["text"],
                    row["posted_at"],
                    row["note"],
                    scalar_texts(json_loads(row["metadata_json"], {}), 4000),
                ]
            ),
        )

    reminder = conn.execute(
        "SELECT * FROM reminders WHERE creator_id = ?",
        (creator_id,),
    ).fetchone()
    if reminder is not None:
        _search_add(
            conn,
            "reminder",
            int(reminder["id"]),
            creator_id,
            reminder["next_due_at"],
            " ".join(
                str(item or "")
                for item in [
                    reminder["next_due_at"],
                    reminder["interval_days"],
                    reminder["note"],
                ]
            ),
        )

    for row in conn.execute(
        "SELECT * FROM worklogs WHERE creator_id = ? ORDER BY id",
        (creator_id,),
    ):
        _search_add(
            conn,
            "work",
            int(row["id"]),
            creator_id,
            row["content"],
            str(row["content"] or ""),
        )


def rebuild_search(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM search_fts")
    creator_ids = [
        int(row["id"])
        for row in conn.execute("SELECT id FROM creators ORDER BY id")
    ]
    for creator_id in creator_ids:
        sync_creator_search(conn, creator_id)


def fts_query(raw: str) -> str | None:
    terms = re.findall(r"[\w@.#:/-]+", raw, flags=re.UNICODE)
    terms = [term.strip('"') for term in terms if term.strip('"')]
    if not terms:
        return None
    escaped = [term.replace('"', '""') for term in terms[:8]]
    return " ".join(f'"{term}"' for term in escaped)


def search_rows(conn: sqlite3.Connection, query: str, limit: int = 30) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    seen: set[tuple[str, int]] = set()
    match = fts_query(query)
    if match:
        try:
            for row in conn.execute(
                """
                SELECT kind, row_id, creator_id, title, body, bm25(search_fts) AS rank
                FROM search_fts
                WHERE search_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, limit),
            ):
                key = (row["kind"], int(row["row_id"]))
                rows.append(row)
                seen.add(key)
        except sqlite3.OperationalError:
            pass
    like = f"%{query}%"
    for row in conn.execute(
        """
        SELECT kind, row_id, creator_id, title, body, 1000.0 AS rank
        FROM search_fts
        WHERE title LIKE ? OR body LIKE ?
        LIMIT ?
        """,
        (like, like, limit),
    ):
        key = (row["kind"], int(row["row_id"]))
        if key in seen:
            continue
        rows.append(row)
        seen.add(key)
        if len(rows) >= limit:
            break
    return rows


def summarize_creator_matches(
    conn: sqlite3.Connection,
    creator_id: int,
    rows: list[sqlite3.Row],
) -> dict[str, Any]:
    creator = conn.execute(
        "SELECT id, primary_name, status FROM creators WHERE id = ?",
        (creator_id,),
    ).fetchone()
    if creator is None:
        raise UserError(f"Creator not found: #{creator_id}")
    kind_counts: dict[str, int] = {}
    snippets: list[str] = []
    seen_snippets: set[str] = set()
    for row in rows:
        kind = str(row["kind"])
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        title = shorten(row["title"], 70)
        if title and title != creator["primary_name"] and title not in seen_snippets:
            seen_snippets.add(title)
            snippets.append(f"{kind}:{title}")
        if len(snippets) >= 4:
            break

    aliases = [
        row["name"]
        for row in conn.execute(
            """
            SELECT DISTINCT name
            FROM creator_aliases
            WHERE creator_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 5
            """,
            (creator_id,),
        )
    ]
    profiles = [
        profile_brief(
            row["platform"],
            row["platform_id"],
            row["display_name"],
            row["primary_url"],
        )
        for row in profile_rows_by_creator(conn, creator_id)[:4]
    ]
    urls = [
        row["url"]
        for row in conn.execute(
            """
            SELECT u.url
            FROM profile_urls u
            JOIN creator_profiles p ON p.id = u.profile_id
            WHERE p.creator_id = ?
            ORDER BY u.updated_at DESC, u.id DESC
            LIMIT 3
            """,
            (creator_id,),
        )
    ]
    return {
        "creator": creator,
        "kind_counts": kind_counts,
        "snippets": snippets,
        "aliases": aliases,
        "profiles": profiles,
        "urls": urls,
    }


def grouped_search(conn: sqlite3.Connection, query: str, limit: int = 12) -> list[dict[str, Any]]:
    rows = search_rows(conn, query, limit=limit * 8)
    grouped: dict[int, list[sqlite3.Row]] = {}
    first_rank: dict[int, float] = {}
    for index, row in enumerate(rows):
        creator_id = int(row["creator_id"])
        grouped.setdefault(creator_id, []).append(row)
        first_rank.setdefault(creator_id, float(row["rank"]) + index * 0.0001)
    creator_ids = sorted(grouped, key=lambda creator_id: first_rank[creator_id])[:limit]
    return [summarize_creator_matches(conn, creator_id, grouped[creator_id]) for creator_id in creator_ids]


def fetch_creator_snapshot(conn: sqlite3.Connection, creator_id: int) -> dict[str, Any]:
    creator = conn.execute("SELECT * FROM creators WHERE id = ?", (creator_id,)).fetchone()
    if creator is None:
        raise UserError(f"Creator not found: #{creator_id}")

    aliases = [
        dict(row)
        for row in conn.execute(
            """
            SELECT a.*,
                   p.platform,
                   p.platform_id,
                   p.display_name
            FROM creator_aliases a
            LEFT JOIN creator_profiles p ON p.id = a.profile_id
            WHERE a.creator_id = ?
            ORDER BY a.updated_at DESC, a.id DESC
            """,
            (creator_id,),
        )
    ]

    profiles: list[dict[str, Any]] = []
    for row in profile_rows_by_creator(conn, creator_id):
        profile_data = dict(row)
        profile_data["urls"] = [dict(url_row) for url_row in profile_url_rows_for_profile(conn, int(row["id"]))]
        profiles.append(profile_data)

    reminder = conn.execute(
        "SELECT id, next_due_at, interval_days, note FROM reminders WHERE creator_id = ?",
        (creator_id,),
    ).fetchone()
    posts = conn.execute(
        """
        SELECT p.*,
               cp.platform,
               cp.platform_id,
               cp.display_name
        FROM posts p
        JOIN creator_profiles cp ON cp.id = p.profile_id
        WHERE p.creator_id = ?
        ORDER BY p.id DESC
        LIMIT 10
        """,
        (creator_id,),
    ).fetchall()
    work = conn.execute(
        """
        SELECT id, content, created_at, updated_at
        FROM worklogs
        WHERE creator_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (creator_id,),
    ).fetchall()
    return {
        "creator": creator,
        "aliases": aliases,
        "profiles": profiles,
        "reminder": reminder,
        "posts": posts,
        "work": work,
    }


def fetch_post_detail_row(conn: sqlite3.Connection, post_id: int) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT p.*,
               c.primary_name,
               cp.platform,
               cp.platform_id,
               cp.display_name
        FROM posts p
        JOIN creators c ON c.id = p.creator_id
        JOIN creator_profiles cp ON cp.id = p.profile_id
        WHERE p.id = ?
        """,
        (post_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"Post not found: {post_id}")
    return row


def fetch_work_detail_row(conn: sqlite3.Connection, work_id: int) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT w.*, c.primary_name
        FROM worklogs w
        JOIN creators c ON c.id = w.creator_id
        WHERE w.id = ?
        """,
        (work_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"Worklog not found: {work_id}")
    return row


def due_rows(conn: sqlite3.Connection, include_future: bool = False) -> list[sqlite3.Row]:
    if include_future:
        return conn.execute(
            """
            SELECT r.*, c.primary_name
            FROM reminders r
            JOIN creators c ON c.id = r.creator_id
            ORDER BY r.next_due_at ASC
            """
        ).fetchall()
    return conn.execute(
        """
        SELECT r.*, c.primary_name
        FROM reminders r
        JOIN creators c ON c.id = r.creator_id
        WHERE r.next_due_at <= ?
        ORDER BY r.next_due_at ASC
        """,
        (today().isoformat(),),
    ).fetchall()


def recent_creators(conn: sqlite3.Connection, limit: int, offset: int = 0) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT c.id,
               c.primary_name,
               c.status,
               c.created_at,
               c.updated_at,
               (SELECT count(*) FROM creator_profiles p WHERE p.creator_id = c.id) AS profile_count,
               (
                   SELECT count(*)
                   FROM profile_urls u
                   JOIN creator_profiles p ON p.id = u.profile_id
                   WHERE p.creator_id = c.id
               ) AS url_count,
               (SELECT count(*) FROM posts p WHERE p.creator_id = c.id) AS post_count,
               (SELECT count(*) FROM worklogs w WHERE w.creator_id = c.id) AS work_count
        FROM creators c
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()


def recent_posts(conn: sqlite3.Connection, limit: int, offset: int = 0) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.*,
               c.primary_name,
               cp.platform,
               cp.platform_id,
               cp.display_name
        FROM posts p
        JOIN creators c ON c.id = p.creator_id
        JOIN creator_profiles cp ON cp.id = p.profile_id
        ORDER BY p.captured_at DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()


def recent_worklogs(conn: sqlite3.Connection, limit: int, offset: int = 0) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT w.*, c.primary_name
        FROM worklogs w
        JOIN creators c ON c.id = w.creator_id
        ORDER BY w.created_at DESC, w.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()


# ── per-creator maintenance reads ──────────────────────────────────────────
def list_creator_urls(conn: sqlite3.Connection, creator_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT u.id           AS url_id,
               u.url,
               u.canonical_url,
               u.from_url,
               u.reason,
               u.status,
               u.note,
               u.created_at,
               u.updated_at,
               p.id           AS profile_id,
               p.platform,
               p.platform_id,
               p.display_name,
               p.identity_state
        FROM profile_urls u
        JOIN creator_profiles p ON p.id = u.profile_id
        WHERE p.creator_id = ?
        ORDER BY p.platform, u.updated_at DESC, u.id DESC
        """,
        (creator_id,),
    ).fetchall()


def list_creator_aliases(conn: sqlite3.Connection, creator_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT a.id,
               a.creator_id,
               a.profile_id,
               a.name,
               a.reason,
               a.status,
               a.note,
               a.from_name,
               a.created_at,
               a.updated_at,
               p.platform,
               p.platform_id,
               p.display_name
        FROM creator_aliases a
        LEFT JOIN creator_profiles p ON p.id = a.profile_id
        WHERE a.creator_id = ?
        ORDER BY a.updated_at DESC, a.id DESC
        """,
        (creator_id,),
    ).fetchall()


def list_creator_posts(conn: sqlite3.Connection, creator_id: int, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.*,
               cp.platform,
               cp.platform_id,
               cp.display_name
        FROM posts p
        JOIN creator_profiles cp ON cp.id = p.profile_id
        WHERE p.creator_id = ?
        ORDER BY p.posted_at DESC, p.captured_at DESC, p.id DESC
        LIMIT ?
        """,
        (creator_id, limit),
    ).fetchall()


def get_creator_reminder(conn: sqlite3.Connection, creator_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, creator_id, next_due_at, interval_days, note,
               last_shown_at, created_at, updated_at
        FROM reminders
        WHERE creator_id = ?
        """,
        (creator_id,),
    ).fetchone()


def list_creator_worklogs(conn: sqlite3.Connection, creator_id: int, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, creator_id, content, created_at, updated_at
        FROM worklogs
        WHERE creator_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (creator_id, limit),
    ).fetchall()


# ── post meta history ──────────────────────────────────────────────────────
def list_post_meta_history(conn: sqlite3.Connection, post_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, post_id, captured_at, source, raw_json
        FROM post_meta_history
        WHERE post_id = ?
        ORDER BY captured_at DESC, id DESC
        """,
        (post_id,),
    ).fetchall()


def fetch_post_meta_history_row(conn: sqlite3.Connection, history_id: int) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, post_id, captured_at, source, raw_json
        FROM post_meta_history
        WHERE id = ?
        """,
        (history_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"Post meta history not found: {history_id}")
    return row


def insert_post_meta_history(
    conn: sqlite3.Connection,
    post_id: int,
    raw_json: str,
    *,
    source: str = "gdl",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO post_meta_history(post_id, captured_at, source, raw_json)
        VALUES(?, ?, ?, ?)
        """,
        (post_id, now_iso(), source, raw_json),
    )
    return require_lastrowid(cur)


def delete_post_meta_history_row(conn: sqlite3.Connection, history_id: int) -> None:
    cur = conn.execute("DELETE FROM post_meta_history WHERE id = ?", (history_id,))
    if cur.rowcount == 0:
        raise UserError(f"Post meta history not found: {history_id}")


# ── url / alias / post / worklog / reminder mutations ─────────────────────
def _get_profile_url_creator(conn: sqlite3.Connection, url_id: int) -> int:
    row = conn.execute(
        """
        SELECT p.creator_id
        FROM profile_urls u
        JOIN creator_profiles p ON p.id = u.profile_id
        WHERE u.id = ?
        """,
        (url_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"Profile URL not found: {url_id}")
    return int(row["creator_id"])


def update_profile_url_fields(
    conn: sqlite3.Connection,
    url_id: int,
    *,
    url: str | None = None,
    note: str | None = None,
    status: str | None = None,
) -> int:
    """Update the URL row's user-editable fields. Returns owning creator_id.

    Recanonicalises ``url`` when supplied. ``note`` and ``status`` use direct
    overwrite semantics — the caller is expected to preserve prior values
    when omitted, since most callers come from edit forms with the full
    current state.
    """
    creator_id = _get_profile_url_creator(conn, url_id)
    ts = now_iso()
    if url is not None:
        canon = canonical_url(url)
        if not canon:
            raise UserError(f"Invalid URL: {url}")
        existing = conn.execute(
            "SELECT id FROM profile_urls WHERE canonical_url = ? AND id != ?",
            (canon, url_id),
        ).fetchone()
        if existing is not None:
            raise ConflictError(f"URL is already linked to another profile: {url}")
        conn.execute(
            "UPDATE profile_urls SET url = ?, canonical_url = ?, updated_at = ? WHERE id = ?",
            (url, canon, ts, url_id),
        )
    conn.execute(
        """
        UPDATE profile_urls
        SET note = ?,
            status = coalesce(?, status),
            updated_at = ?
        WHERE id = ?
        """,
        (note, status, ts, url_id),
    )
    return creator_id


def delete_profile_url_row(conn: sqlite3.Connection, url_id: int) -> int:
    """Delete a URL row; orphan profile/cleanup on best-effort. Returns creator_id."""
    creator_id = _get_profile_url_creator(conn, url_id)
    profile_id_row = conn.execute(
        "SELECT profile_id FROM profile_urls WHERE id = ?",
        (url_id,),
    ).fetchone()
    if profile_id_row is None:
        raise UserError(f"Profile URL not found: {url_id}")
    profile_id = int(profile_id_row["profile_id"])
    conn.execute("DELETE FROM profile_urls WHERE id = ?", (url_id,))
    delete_profile_if_orphaned(conn, profile_id)
    return creator_id


def update_alias_fields(
    conn: sqlite3.Connection,
    alias_id: int,
    *,
    name: str,
    reason: str | None = None,
    note: str | None = None,
    status: str | None = None,
) -> int:
    row = conn.execute(
        "SELECT creator_id FROM creator_aliases WHERE id = ?",
        (alias_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"Alias not found: {alias_id}")
    creator_id = int(row["creator_id"])
    if not name.strip():
        raise UserError("name is required")
    ts = now_iso()
    conn.execute(
        """
        UPDATE creator_aliases
        SET name = ?,
            reason = coalesce(?, reason),
            status = coalesce(?, status),
            note = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (name.strip(), reason, status, note, ts, alias_id),
    )
    return creator_id


def delete_alias_row(conn: sqlite3.Connection, alias_id: int) -> int:
    row = conn.execute(
        "SELECT creator_id FROM creator_aliases WHERE id = ?",
        (alias_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"Alias not found: {alias_id}")
    creator_id = int(row["creator_id"])
    conn.execute("DELETE FROM creator_aliases WHERE id = ?", (alias_id,))
    return creator_id


def update_post_fields(
    conn: sqlite3.Connection,
    post_id: int,
    *,
    url: str | None = None,
    note: str | None = None,
    title: str | None = None,
) -> int:
    row = conn.execute(
        "SELECT creator_id FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"Post not found: {post_id}")
    creator_id = int(row["creator_id"])
    ts = now_iso()
    if url is not None:
        canon = canonical_url(url)
        if not canon:
            raise UserError(f"Invalid URL: {url}")
        existing = conn.execute(
            "SELECT id, creator_id FROM posts WHERE canonical_url = ? AND id != ?",
            (canon, post_id),
        ).fetchone()
        if existing is not None:
            raise ConflictError(
                f"Post URL is already linked to {creator_label(conn, int(existing['creator_id']))}: {url}"
            )
        conn.execute(
            "UPDATE posts SET url = ?, canonical_url = ?, updated_at = ? WHERE id = ?",
            (url, canon, ts, post_id),
        )
    conn.execute(
        """
        UPDATE posts
        SET note = ?,
            title = coalesce(?, title),
            updated_at = ?
        WHERE id = ?
        """,
        (note, title, ts, post_id),
    )
    return creator_id


def delete_post_row(conn: sqlite3.Connection, post_id: int) -> int:
    row = conn.execute(
        "SELECT creator_id FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"Post not found: {post_id}")
    creator_id = int(row["creator_id"])
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    return creator_id


def replace_post_metadata(conn: sqlite3.Connection, post_id: int, metadata_json: str) -> None:
    """Overwrite posts.metadata_json without touching the history table."""
    conn.execute(
        "UPDATE posts SET metadata_json = ?, updated_at = ? WHERE id = ?",
        (metadata_json, now_iso(), post_id),
    )


def update_worklog_content(conn: sqlite3.Connection, work_id: int, content: str) -> int:
    row = conn.execute(
        "SELECT creator_id FROM worklogs WHERE id = ?",
        (work_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"Worklog not found: {work_id}")
    creator_id = int(row["creator_id"])
    if not content.strip():
        raise UserError("content is required")
    conn.execute(
        "UPDATE worklogs SET content = ?, updated_at = ? WHERE id = ?",
        (content, now_iso(), work_id),
    )
    return creator_id


def delete_worklog_row(conn: sqlite3.Connection, work_id: int) -> int:
    row = conn.execute(
        "SELECT creator_id FROM worklogs WHERE id = ?",
        (work_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"Worklog not found: {work_id}")
    creator_id = int(row["creator_id"])
    conn.execute("DELETE FROM worklogs WHERE id = ?", (work_id,))
    return creator_id


def update_reminder_fields(
    conn: sqlite3.Connection,
    creator_id: int,
    *,
    next_due_at: str,
    interval_days: int | None,
    note: str | None,
) -> None:
    row = conn.execute(
        "SELECT id FROM reminders WHERE creator_id = ?",
        (creator_id,),
    ).fetchone()
    if row is None:
        raise UserError(f"No reminder for creator #{creator_id}")
    conn.execute(
        """
        UPDATE reminders
        SET next_due_at = ?,
            interval_days = ?,
            note = ?,
            updated_at = ?
        WHERE creator_id = ?
        """,
        (next_due_at, interval_days, note, now_iso(), creator_id),
    )


def delete_reminder_row(conn: sqlite3.Connection, creator_id: int) -> bool:
    cur = conn.execute("DELETE FROM reminders WHERE creator_id = ?", (creator_id,))
    return cur.rowcount > 0

