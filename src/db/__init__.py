from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from core.config import LOGGER
from core.constants import AmbiguousTarget, ConflictError, SCHEMA_VERSION, UserError
from core.utils import (
    canonical_url,
    infer_platform_from_url,
    is_url,
    json_dumps,
    json_loads,
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

        CREATE TABLE IF NOT EXISTS profile_enrichment_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_url_id INTEGER NOT NULL UNIQUE REFERENCES profile_urls(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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
        CREATE INDEX IF NOT EXISTS idx_profile_jobs_status ON profile_enrichment_jobs(status, attempts);

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
    conn.commit()


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


def upsert_profile_job(conn: sqlite3.Connection, profile_url_id: int) -> None:
    row = conn.execute(
        "SELECT id, status FROM profile_enrichment_jobs WHERE profile_url_id = ?",
        (profile_url_id,),
    ).fetchone()
    ts = now_iso()
    if row:
        if row["status"] == "done":
            return
        conn.execute(
            """
            UPDATE profile_enrichment_jobs
            SET status = 'pending',
                last_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (ts, row["id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO profile_enrichment_jobs(
            profile_url_id, status, attempts, last_error, created_at, updated_at
        )
        VALUES(?, 'pending', 0, NULL, ?, ?)
        """,
        (profile_url_id, ts, ts),
    )


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
