from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Callable, ClassVar, Iterator

from db import (
    candidate_lines,
    connect,
    due_rows,
    fetch_creator_snapshot,
    fetch_post_detail_row,
    fetch_work_detail_row,
    grouped_search,
    rebuild_search,
    recent_creators,
    recent_posts,
    recent_worklogs,
    resolve_target,
    search_rows,
)
from .config import LOGGER, load_config, write_default_config
from .enrichment import InProcessRunner
from .jobs import JobPool
from .pubsub import PubSub
from .service import (
    add_creator_record,
    add_name_record,
    add_post_record,
    add_url_record,
    add_work_record,
    set_reminder_record,
)


ReadFn = Callable[..., Any]
WriteFn = Callable[..., Any]


class Controller:
    """Single point of truth for the GUI: db connection, pub/sub, background jobs."""

    instance: ClassVar["Controller | None"] = None

    def __init__(self, db_path: Path, *, headless: bool = False) -> None:
        write_default_config()
        self.db_path = db_path
        self.options: dict[str, Any] = load_config()
        self.pubsub = PubSub()
        self.jobs = JobPool()
        self._db_lock = RLock()
        self._conn = connect(db_path)
        self._headless = headless
        self._enrichment: InProcessRunner | None = None if headless else InProcessRunner(self)
        self._read_actions = _build_read_actions()
        self._write_actions = _build_write_actions()
        Controller.instance = self
        LOGGER.info("Controller boot db=%s headless=%s", db_path, headless)

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        with self._db_lock:
            yield self._conn

    def read(self, action: str, *args: Any, **kwargs: Any) -> Any:
        fn = self._read_actions.get(action)
        if fn is None:
            raise KeyError(f"unknown read action: {action}")
        with self.db() as conn:
            return fn(conn, *args, **kwargs)

    def write(self, action: str, *args: Any, **kwargs: Any) -> Any:
        fn = self._write_actions.get(action)
        if fn is None:
            raise KeyError(f"unknown write action: {action}")
        with self.db() as conn:
            result = fn(conn, self.db_path, *args, **kwargs)
        if isinstance(result, dict) and result.get("needs_worker") and self._enrichment is not None:
            self._enrichment.kick()
        self.pubsub.pub(f"data.{action}", result=result)
        return result

    def kick_enrichment(self) -> None:
        if self._enrichment is not None:
            self._enrichment.kick()

    def shutdown(self) -> None:
        LOGGER.info("Controller shutdown begin")
        try:
            self.jobs.shutdown(wait=True)
        except Exception as exc:
            LOGGER.warning("JobPool shutdown failed: %s", exc)
        with self._db_lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as exc:
                LOGGER.warning("DB checkpoint failed: %s", exc)
            self._conn.close()
        if Controller.instance is self:
            Controller.instance = None
        LOGGER.info("Controller shutdown done")


def _build_read_actions() -> dict[str, ReadFn]:
    return {
        "recent_creators": recent_creators,
        "recent_posts": recent_posts,
        "recent_worklogs": recent_worklogs,
        "creator_snapshot": fetch_creator_snapshot,
        "post_detail": fetch_post_detail_row,
        "work_detail": fetch_work_detail_row,
        "grouped_search": grouped_search,
        "search_rows": search_rows,
        "due": due_rows,
        "candidate_lines": candidate_lines,
        "resolve_target": resolve_target,
    }


def _build_write_actions() -> dict[str, WriteFn]:
    def init_action(conn: sqlite3.Connection, _db_path: Path) -> dict[str, Any]:
        rebuild_search(conn)
        conn.commit()
        return {"ok": True}

    def remind_action(conn: sqlite3.Connection, _db_path: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return set_reminder_record(conn, *args, **kwargs)

    def work_action(conn: sqlite3.Connection, _db_path: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return add_work_record(conn, *args, **kwargs)

    return {
        "init": init_action,
        "add_creator": add_creator_record,
        "add_name": add_name_record,
        "add_url": add_url_record,
        "add_post": add_post_record,
        "set_reminder": remind_action,
        "add_work": work_action,
    }
