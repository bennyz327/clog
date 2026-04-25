from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .config import LOGGER
from .constants import (
    SCHEMA_VERSION,
    AmbiguousTarget,
    ConflictError,
    UserError,
)
from .utils import (
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
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS creators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            primary_name TEXT NOT NULL,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS creator_names (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            platform TEXT,
            platform_id TEXT,
            url TEXT,
            canonical_url TEXT,
            from_name TEXT,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            note TEXT,
            metadata_json TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS creator_urls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL UNIQUE,
            platform TEXT,
            platform_id TEXT,
            name TEXT,
            from_url TEXT,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            note TEXT,
            metadata_json TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS platform_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
            platform TEXT NOT NULL,
            platform_id TEXT NOT NULL,
            display_name TEXT,
            profile_url TEXT,
            profile_canonical_url TEXT,
            source TEXT,
            metadata_json TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(platform, platform_id)
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL UNIQUE,
            platform TEXT,
            platform_post_id TEXT,
            author_platform_id TEXT,
            author_name TEXT,
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
            message TEXT NOT NULL,
            tags_json TEXT,
            paths_json TEXT,
            urls_json TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS metadata_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
            target_kind TEXT NOT NULL,
            target_row_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_creator_names_creator ON creator_names(creator_id);
        CREATE INDEX IF NOT EXISTS idx_creator_names_name ON creator_names(name);
        CREATE INDEX IF NOT EXISTS idx_creator_urls_creator ON creator_urls(creator_id);
        CREATE INDEX IF NOT EXISTS idx_creator_urls_status ON creator_urls(status);
        CREATE INDEX IF NOT EXISTS idx_platform_accounts_creator ON platform_accounts(creator_id);
        CREATE INDEX IF NOT EXISTS idx_platform_accounts_profile ON platform_accounts(profile_canonical_url);
        CREATE INDEX IF NOT EXISTS idx_posts_creator ON posts(creator_id);
        CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(next_due_at);
        CREATE INDEX IF NOT EXISTS idx_worklogs_creator ON worklogs(creator_id);
        CREATE INDEX IF NOT EXISTS idx_metadata_tasks_status ON metadata_tasks(status, attempts);

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
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


# ── creator lookup ────────────────────────────────────────────────────────────

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


def candidate_lines(conn: sqlite3.Connection, creator_ids: list[int]) -> list[str]:
    lines: list[str] = []
    for cid in creator_ids[:10]:
        creator = conn.execute(
            "SELECT id, primary_name, status FROM creators WHERE id = ?", (cid,)
        ).fetchone()
        if not creator:
            continue
        names = [
            r["name"]
            for r in conn.execute(
                "SELECT DISTINCT name FROM creator_names WHERE creator_id = ? ORDER BY id DESC LIMIT 4",
                (cid,),
            )
        ]
        accounts = [
            f"{r['platform']}:{r['platform_id']}"
            for r in conn.execute(
                "SELECT platform, platform_id FROM platform_accounts WHERE creator_id = ? ORDER BY updated_at DESC LIMIT 3",
                (cid,),
            )
        ]
        urls = [
            shorten(r["url"], 60)
            for r in conn.execute(
                "SELECT url FROM creator_urls WHERE creator_id = ? ORDER BY updated_at DESC LIMIT 2",
                (cid,),
            )
        ]
        detail = " | ".join(part for part in [", ".join(names), ", ".join(accounts), ", ".join(urls)] if part)
        lines.append(f"  #{creator['id']} {creator['primary_name']}" + (f" ({detail})" if detail else ""))
    if len(creator_ids) > 10:
        lines.append(f"  ... and {len(creator_ids) - 10} more")
    return lines


def platform_account_creator(
    conn: sqlite3.Connection, platform: str | None, platform_id: str | None
) -> int | None:
    platform = normalize_platform(platform)
    platform_id = normalize_pid(platform_id)
    if not platform or not platform_id:
        return None
    row = conn.execute(
        "SELECT creator_id FROM platform_accounts WHERE platform = ? AND platform_id = ?",
        (platform, platform_id),
    ).fetchone()
    return int(row["creator_id"]) if row else None


def creator_ids_by_url(conn: sqlite3.Connection, value: str) -> list[int]:
    canon = canonical_url(value)
    if not canon:
        return []
    ids: list[int] = []
    for row in conn.execute("SELECT creator_id FROM creator_urls WHERE canonical_url = ?", (canon,)):
        ids.append(int(row["creator_id"]))
    for row in conn.execute(
        "SELECT creator_id FROM platform_accounts WHERE profile_canonical_url = ?", (canon,)
    ):
        ids.append(int(row["creator_id"]))
    return sorted(set(ids))


def creator_ids_by_name(conn: sqlite3.Connection, value: str) -> list[int]:
    q = value.strip()
    if not q:
        return []
    exact: set[int] = set()
    for row in conn.execute("SELECT id FROM creators WHERE lower(primary_name) = lower(?)", (q,)):
        exact.add(int(row["id"]))
    for row in conn.execute("SELECT creator_id FROM creator_names WHERE lower(name) = lower(?)", (q,)):
        exact.add(int(row["creator_id"]))
    if exact:
        return sorted(exact)
    like = f"%{q}%"
    fuzzy: set[int] = set()
    for row in conn.execute("SELECT id FROM creators WHERE primary_name LIKE ? LIMIT 20", (like,)):
        fuzzy.add(int(row["id"]))
    for row in conn.execute("SELECT creator_id FROM creator_names WHERE name LIKE ? LIMIT 20", (like,)):
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


def resolve_target(
    conn: sqlite3.Connection,
    target: str,
    *,
    platform: str | None = None,
    platform_id: str | None = None,
) -> int:
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
        cid = platform_account_creator(conn, parsed[0], parsed[1])
        if cid is None:
            raise UserError(f"No creator is linked to platform id: {target}")
        return cid

    cid = platform_account_creator(conn, platform, platform_id)
    if cid is not None:
        return cid

    ids = creator_ids_by_name(conn, target)
    if len(ids) == 1:
        return ids[0]
    if len(ids) > 1:
        raise AmbiguousTarget(target, ids)
    raise UserError(f"Creator not found: {target}")


# ── write operations ──────────────────────────────────────────────────────────

def insert_creator(conn: sqlite3.Connection, name: str, note: str | None = None) -> int:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO creators(primary_name, note, created_at, updated_at) VALUES(?, ?, ?, ?)",
        (name, note, ts, ts),
    )
    creator_id = int(cur.lastrowid)
    insert_name_fact(conn, creator_id, name, reason="initial", note=note)
    return creator_id


def insert_name_fact(
    conn: sqlite3.Connection,
    creator_id: int,
    name: str,
    *,
    platform: str | None = None,
    platform_id: str | None = None,
    url: str | None = None,
    from_name: str | None = None,
    reason: str | None = None,
    status: str = "active",
    note: str | None = None,
    metadata: Any = None,
) -> int:
    platform = normalize_platform(platform)
    platform_id = normalize_pid(platform_id)
    canon = canonical_url(url)
    ts = now_iso()
    existing = conn.execute(
        """
        SELECT id FROM creator_names
        WHERE creator_id = ?
          AND lower(name) = lower(?)
          AND coalesce(platform, '') = coalesce(?, '')
          AND coalesce(platform_id, '') = coalesce(?, '')
          AND coalesce(canonical_url, '') = coalesce(?, '')
        ORDER BY id DESC LIMIT 1
        """,
        (creator_id, name, platform, platform_id, canon),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE creator_names
            SET from_name = coalesce(?, from_name),
                reason = coalesce(?, reason),
                status = coalesce(?, status),
                note = coalesce(?, note),
                metadata_json = coalesce(?, metadata_json),
                last_seen_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (from_name, reason, status, note, json_dumps(metadata), ts, ts, existing["id"]),
        )
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO creator_names(
            creator_id, name, platform, platform_id, url, canonical_url,
            from_name, reason, status, note, metadata_json,
            first_seen_at, last_seen_at, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (creator_id, name, platform, platform_id, url, canon, from_name, reason, status, note,
         json_dumps(metadata), ts, ts, ts, ts),
    )
    conn.execute("UPDATE creators SET updated_at = ? WHERE id = ?", (ts, creator_id))
    return int(cur.lastrowid)


def upsert_platform_account(
    conn: sqlite3.Connection,
    creator_id: int,
    platform: str | None,
    platform_id: str | None,
    *,
    display_name: str | None = None,
    profile_url: str | None = None,
    source: str | None = None,
    metadata: Any = None,
) -> int | None:
    platform = normalize_platform(platform)
    platform_id = normalize_pid(platform_id)
    if not platform or not platform_id:
        return None
    ts = now_iso()
    profile_canon = canonical_url(profile_url)
    row = conn.execute(
        "SELECT id, creator_id FROM platform_accounts WHERE platform = ? AND platform_id = ?",
        (platform, platform_id),
    ).fetchone()
    if row and int(row["creator_id"]) != creator_id:
        raise ConflictError(
            f"{platform}:{platform_id} is already linked to {creator_label(conn, int(row['creator_id']))}"
        )
    if row:
        conn.execute(
            """
            UPDATE platform_accounts
            SET display_name = coalesce(?, display_name),
                profile_url = coalesce(?, profile_url),
                profile_canonical_url = coalesce(?, profile_canonical_url),
                source = coalesce(?, source),
                metadata_json = coalesce(?, metadata_json),
                last_seen_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (display_name, profile_url, profile_canon, source, json_dumps(metadata), ts, ts, row["id"]),
        )
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO platform_accounts(
            creator_id, platform, platform_id, display_name, profile_url,
            profile_canonical_url, source, metadata_json,
            first_seen_at, last_seen_at, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (creator_id, platform, platform_id, display_name, profile_url, profile_canon,
         source, json_dumps(metadata), ts, ts, ts, ts),
    )
    conn.execute("UPDATE creators SET updated_at = ? WHERE id = ?", (ts, creator_id))
    return int(cur.lastrowid)


def insert_metadata_task(
    conn: sqlite3.Connection,
    creator_id: int,
    target_kind: str,
    target_row_id: int,
    url: str,
) -> None:
    canon = canonical_url(url)
    if not canon:
        return
    row = conn.execute(
        """
        SELECT id FROM metadata_tasks
        WHERE canonical_url = ? AND target_kind = ? AND target_row_id = ?
          AND status IN ('pending', 'done')
        LIMIT 1
        """,
        (canon, target_kind, target_row_id),
    ).fetchone()
    if row:
        return
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO metadata_tasks(
            creator_id, target_kind, target_row_id, url, canonical_url,
            status, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (creator_id, target_kind, target_row_id, url, canon, ts, ts),
    )


def insert_url_fact(
    conn: sqlite3.Connection,
    creator_id: int,
    url: str,
    *,
    platform: str | None = None,
    platform_id: str | None = None,
    name: str | None = None,
    from_url: str | None = None,
    reason: str | None = None,
    status: str = "active",
    note: str | None = None,
    metadata: Any = None,
    schedule_metadata: bool = True,
) -> int:
    canon = canonical_url(url)
    if not canon:
        raise UserError(f"Invalid URL: {url}")
    platform = normalize_platform(platform) or infer_platform_from_url(canon)
    platform_id = normalize_pid(platform_id)
    ts = now_iso()
    existing = conn.execute(
        "SELECT id, creator_id FROM creator_urls WHERE canonical_url = ?", (canon,)
    ).fetchone()
    if existing and int(existing["creator_id"]) != creator_id:
        raise ConflictError(
            f"URL is already linked to {creator_label(conn, int(existing['creator_id']))}: {url}"
        )
    if existing:
        conn.execute(
            """
            UPDATE creator_urls
            SET platform = coalesce(?, platform),
                platform_id = coalesce(?, platform_id),
                name = coalesce(?, name),
                from_url = coalesce(?, from_url),
                reason = coalesce(?, reason),
                status = coalesce(?, status),
                note = coalesce(?, note),
                metadata_json = coalesce(?, metadata_json),
                last_seen_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (platform, platform_id, name, from_url, reason, status, note, json_dumps(metadata),
             ts, ts, existing["id"]),
        )
        url_id = int(existing["id"])
    else:
        cur = conn.execute(
            """
            INSERT INTO creator_urls(
                creator_id, url, canonical_url, platform, platform_id, name,
                from_url, reason, status, note, metadata_json,
                first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (creator_id, url, canon, platform, platform_id, name, from_url, reason, status, note,
             json_dumps(metadata), ts, ts, ts, ts),
        )
        url_id = int(cur.lastrowid)

    if platform and platform_id:
        upsert_platform_account(
            conn, creator_id, platform, platform_id,
            display_name=name, profile_url=url, source="manual-url", metadata=metadata,
        )
    if name:
        insert_name_fact(
            conn, creator_id, name, platform=platform, platform_id=platform_id,
            url=url, reason=reason, status=status, note=note, metadata=metadata,
        )
    if schedule_metadata:
        insert_metadata_task(conn, creator_id, "url", url_id, url)

    conn.execute("UPDATE creators SET updated_at = ? WHERE id = ?", (ts, creator_id))
    return url_id


def insert_or_update_post(
    conn: sqlite3.Connection,
    creator_id: int,
    url: str,
    metadata_info: dict[str, Any] | None,
    *,
    note: str | None = None,
) -> int:
    canon = canonical_url(url)
    if not canon:
        raise UserError(f"Invalid URL: {url}")
    metadata_info = metadata_info or {}
    ts = now_iso()
    existing = conn.execute(
        "SELECT id, creator_id FROM posts WHERE canonical_url = ?", (canon,)
    ).fetchone()
    if existing and int(existing["creator_id"]) != creator_id:
        raise ConflictError(
            f"Post URL is already linked to {creator_label(conn, int(existing['creator_id']))}: {url}"
        )
    values = (
        metadata_info.get("platform"),
        metadata_info.get("post_id"),
        metadata_info.get("platform_id"),
        metadata_info.get("author_name"),
        metadata_info.get("title"),
        metadata_info.get("text"),
        metadata_info.get("posted_at"),
        json_dumps(metadata_info.get("raw")),
        note,
        ts,
        ts,
    )
    if existing:
        conn.execute(
            """
            UPDATE posts
            SET platform = coalesce(?, platform),
                platform_post_id = coalesce(?, platform_post_id),
                author_platform_id = coalesce(?, author_platform_id),
                author_name = coalesce(?, author_name),
                title = coalesce(?, title),
                text = coalesce(?, text),
                posted_at = coalesce(?, posted_at),
                metadata_json = coalesce(?, metadata_json),
                note = coalesce(?, note),
                captured_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            values + (existing["id"],),
        )
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO posts(
            creator_id, url, canonical_url, platform, platform_post_id,
            author_platform_id, author_name, title, text, posted_at,
            captured_at, metadata_json, note, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (creator_id, url, canon, metadata_info.get("platform"), metadata_info.get("post_id"),
         metadata_info.get("platform_id"), metadata_info.get("author_name"),
         metadata_info.get("title"), metadata_info.get("text"), metadata_info.get("posted_at"),
         ts, json_dumps(metadata_info.get("raw")), note, ts, ts),
    )
    conn.execute("UPDATE creators SET updated_at = ? WHERE id = ?", (ts, creator_id))
    return int(cur.lastrowid)


# ── search ────────────────────────────────────────────────────────────────────

def scalar_texts(value: Any, limit: int = 8000) -> str:
    texts: list[str] = []

    def walk(item: Any) -> None:
        if sum(len(x) for x in texts) > limit:
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


def rebuild_search(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM search_fts")

    def add(kind: str, row_id: int, creator_id: int, title: str, body: str) -> None:
        conn.execute(
            "INSERT INTO search_fts(kind, row_id, creator_id, title, body) VALUES(?, ?, ?, ?, ?)",
            (kind, row_id, creator_id, title, body),
        )

    for row in conn.execute("SELECT * FROM creators ORDER BY id"):
        add("creator", row["id"], row["id"], row["primary_name"],
            " ".join([row["primary_name"], row["note"] or "", row["status"] or ""]))

    for row in conn.execute("SELECT * FROM creator_names ORDER BY id"):
        add("name", row["id"], row["creator_id"], row["name"],
            " ".join(str(x or "") for x in [row["name"], row["platform"], row["platform_id"],
            row["url"], row["from_name"], row["reason"], row["status"], row["note"],
            scalar_texts(json_loads(row["metadata_json"], {}), 2000)]))

    for row in conn.execute("SELECT * FROM creator_urls ORDER BY id"):
        add("url", row["id"], row["creator_id"], row["url"],
            " ".join(str(x or "") for x in [row["url"], row["platform"], row["platform_id"],
            row["name"], row["from_url"], row["reason"], row["status"], row["note"],
            scalar_texts(json_loads(row["metadata_json"], {}), 2000)]))

    for row in conn.execute("SELECT * FROM platform_accounts ORDER BY id"):
        add("account", row["id"], row["creator_id"],
            f"{row['platform']}:{row['platform_id']}",
            " ".join(str(x or "") for x in [row["platform"], row["platform_id"],
            row["display_name"], row["profile_url"], row["source"],
            scalar_texts(json_loads(row["metadata_json"], {}), 2000)]))

    for row in conn.execute("SELECT * FROM posts ORDER BY id"):
        add("post", row["id"], row["creator_id"], row["title"] or row["url"],
            " ".join(str(x or "") for x in [row["url"], row["platform"], row["platform_post_id"],
            row["author_platform_id"], row["author_name"], row["title"], row["text"],
            row["posted_at"], row["note"],
            scalar_texts(json_loads(row["metadata_json"], {}), 4000)]))

    for row in conn.execute("SELECT * FROM reminders ORDER BY id"):
        add("reminder", row["id"], row["creator_id"], row["next_due_at"],
            " ".join(str(x or "") for x in [row["next_due_at"], row["interval_days"], row["note"]]))

    for row in conn.execute("SELECT * FROM worklogs ORDER BY id"):
        add("work", row["id"], row["creator_id"], row["message"],
            " ".join(str(x or "") for x in [row["message"],
            scalar_texts(json_loads(row["tags_json"], []), 1000),
            scalar_texts(json_loads(row["paths_json"], []), 1000),
            scalar_texts(json_loads(row["urls_json"], []), 1000),
            scalar_texts(json_loads(row["metadata_json"], {}), 2000)]))


def fts_query(raw: str) -> str | None:
    terms = re.findall(r"[\w@.#:/-]+", raw, flags=re.UNICODE)
    terms = [t.strip('"') for t in terms if t.strip('"')]
    if not terms:
        return None
    escaped = [t.replace('"', '""') for t in terms[:8]]
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
                FROM search_fts WHERE search_fts MATCH ?
                ORDER BY rank LIMIT ?
                """,
                (match, limit),
            ):
                key = (row["kind"], int(row["row_id"]))
                seen.add(key)
                rows.append(row)
        except sqlite3.OperationalError:
            pass
    like = f"%{query}%"
    for row in conn.execute(
        "SELECT kind, row_id, creator_id, title, body, 1000.0 AS rank FROM search_fts WHERE title LIKE ? OR body LIKE ? LIMIT ?",
        (like, like, limit),
    ):
        key = (row["kind"], int(row["row_id"]))
        if key not in seen:
            rows.append(row)
            seen.add(key)
        if len(rows) >= limit:
            break
    return rows


def summarize_creator_matches(
    conn: sqlite3.Connection, creator_id: int, rows: list[sqlite3.Row]
) -> dict[str, Any]:
    creator = conn.execute(
        "SELECT id, primary_name, status FROM creators WHERE id = ?", (creator_id,)
    ).fetchone()
    kind_counts: dict[str, int] = {}
    snippets: list[str] = []
    seen_snippets: set[str] = set()
    for row in rows:
        kind = str(row["kind"])
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        title = shorten(row["title"], 70)
        if title and title not in seen_snippets and title != creator["primary_name"]:
            seen_snippets.add(title)
            snippets.append(f"{kind}:{title}")
        if len(snippets) >= 4:
            break
    names = [r["name"] for r in conn.execute(
        "SELECT DISTINCT name FROM creator_names WHERE creator_id = ? ORDER BY id DESC LIMIT 5",
        (creator_id,))]
    accounts = [f"{r['platform']}:{r['platform_id']}" for r in conn.execute(
        "SELECT platform, platform_id FROM platform_accounts WHERE creator_id = ? ORDER BY updated_at DESC LIMIT 4",
        (creator_id,))]
    urls = [(r["platform"], r["url"]) for r in conn.execute(
        "SELECT platform, url FROM creator_urls WHERE creator_id = ? ORDER BY updated_at DESC LIMIT 3",
        (creator_id,))]
    return {"creator": creator, "kind_counts": kind_counts, "snippets": snippets,
            "names": names, "accounts": accounts, "urls": urls}


def grouped_search(conn: sqlite3.Connection, query: str, limit: int = 12) -> list[dict[str, Any]]:
    rows = search_rows(conn, query, limit=limit * 8)
    grouped: dict[int, list[sqlite3.Row]] = {}
    first_rank: dict[int, float] = {}
    for index, row in enumerate(rows):
        creator_id = int(row["creator_id"])
        grouped.setdefault(creator_id, []).append(row)
        first_rank.setdefault(creator_id, float(row["rank"]) + index * 0.0001)
    creator_ids = sorted(grouped, key=lambda cid: first_rank[cid])[:limit]
    return [summarize_creator_matches(conn, cid, grouped[cid]) for cid in creator_ids]


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_creator_snapshot(conn: sqlite3.Connection, creator_id: int) -> dict[str, Any]:
    creator = conn.execute("SELECT * FROM creators WHERE id = ?", (creator_id,)).fetchone()
    if not creator:
        raise UserError(f"Creator not found: #{creator_id}")
    return {
        "creator": creator,
        "names": conn.execute(
            "SELECT id, name, platform, platform_id, reason, status, from_name, note FROM creator_names WHERE creator_id = ? ORDER BY id DESC",
            (creator_id,),
        ).fetchall(),
        "urls": conn.execute(
            "SELECT id, url, platform, platform_id, name, reason, status, from_url, note FROM creator_urls WHERE creator_id = ? ORDER BY id DESC",
            (creator_id,),
        ).fetchall(),
        "accounts": conn.execute(
            "SELECT id, platform, platform_id, display_name, profile_url, source, last_seen_at FROM platform_accounts WHERE creator_id = ? ORDER BY updated_at DESC",
            (creator_id,),
        ).fetchall(),
        "reminder": conn.execute(
            "SELECT id, next_due_at, interval_days, note FROM reminders WHERE creator_id = ?",
            (creator_id,),
        ).fetchone(),
        "posts": conn.execute(
            "SELECT id, url, platform, platform_post_id, author_platform_id, author_name, title, posted_at FROM posts WHERE creator_id = ? ORDER BY id DESC LIMIT 10",
            (creator_id,),
        ).fetchall(),
        "work": conn.execute(
            "SELECT id, message, tags_json, paths_json, urls_json, created_at FROM worklogs WHERE creator_id = ? ORDER BY id DESC LIMIT 10",
            (creator_id,),
        ).fetchall(),
    }


def fetch_post_detail_row(conn: sqlite3.Connection, post_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT p.*, c.primary_name FROM posts p JOIN creators c ON c.id = p.creator_id WHERE p.id = ?",
        (post_id,),
    ).fetchone()
    if not row:
        raise UserError(f"Post not found: {post_id}")
    return row


def fetch_work_detail_row(conn: sqlite3.Connection, work_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT w.*, c.primary_name FROM worklogs w JOIN creators c ON c.id = w.creator_id WHERE w.id = ?",
        (work_id,),
    ).fetchone()
    if not row:
        raise UserError(f"Worklog not found: {work_id}")
    return row


def due_rows(conn: sqlite3.Connection, include_future: bool = False) -> list[sqlite3.Row]:
    if include_future:
        return conn.execute(
            "SELECT r.*, c.primary_name FROM reminders r JOIN creators c ON c.id = r.creator_id ORDER BY r.next_due_at ASC"
        ).fetchall()
    return conn.execute(
        "SELECT r.*, c.primary_name FROM reminders r JOIN creators c ON c.id = r.creator_id WHERE r.next_due_at <= ? ORDER BY r.next_due_at ASC",
        (today().isoformat(),),
    ).fetchall()


def recent_creators(conn: sqlite3.Connection, limit: int, offset: int = 0) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT c.id, c.primary_name, c.status, c.created_at, c.updated_at,
               (SELECT count(*) FROM posts p WHERE p.creator_id = c.id) AS post_count,
               (SELECT count(*) FROM worklogs w WHERE w.creator_id = c.id) AS work_count,
               (SELECT count(*) FROM creator_urls u WHERE u.creator_id = c.id) AS url_count
        FROM creators c
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()


def recent_posts(conn: sqlite3.Connection, limit: int, offset: int = 0) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.*, c.primary_name FROM posts p
        JOIN creators c ON c.id = p.creator_id
        ORDER BY p.captured_at DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()


def recent_worklogs(conn: sqlite3.Connection, limit: int, offset: int = 0) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT w.*, c.primary_name FROM worklogs w
        JOIN creators c ON c.id = w.creator_id
        ORDER BY w.created_at DESC, w.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
