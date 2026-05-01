from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
import time
from typing import Any, Callable, ClassVar, Iterator

from db import (
    cancel_queued_background_job,
    cancel_running_background_job,
    candidate_lines,
    connect,
    due_rows,
    fetch_background_job,
    fetch_creator_snapshot,
    fetch_post_detail_row,
    fetch_post_meta_history_row,
    fetch_work_detail_row,
    get_app_setting,
    get_creator_reminder,
    grouped_search,
    list_app_settings,
    list_creator_aliases,
    list_creator_posts,
    list_creator_urls,
    list_creator_worklogs,
    list_background_job_events,
    list_background_jobs,
    list_post_meta_history,
    rebuild_search,
    request_terminate_background_job,
    retry_background_job,
    recent_creators,
    recent_posts,
    recent_worklogs,
    resolve_target,
    search_rows,
    set_app_setting,
)
from .background_runtime import BackgroundRuntime
from .config import LOGGER, write_default_config
from .pubsub import PubSub
from .service import (
    add_creator_record,
    add_name_record,
    add_post_record,
    add_url_record,
    add_work_record,
    delete_alias_record,
    delete_post_meta_history_record,
    delete_post_record,
    delete_reminder_record,
    delete_url_record,
    delete_work_record,
    refetch_post_meta_record,
    set_reminder_record,
    update_alias_record,
    update_post_record,
    update_reminder_record,
    update_url_record,
    update_work_record,
)


ReadFn = Callable[..., Any]
WriteFn = Callable[..., Any]


class Controller:
    """Single point of truth for the GUI: db connection, pub/sub, background jobs."""

    instance: ClassVar["Controller | None"] = None

    def __init__(self, db_path: Path, *, headless: bool = False) -> None:
        write_default_config()
        self.db_path = db_path
        os.environ["CLOG_DB_PATH"] = str(db_path)
        self.pubsub = PubSub()
        self._db_lock = RLock()
        self._conn = connect(db_path)
        self._headless = headless
        self._last_user_activity = time.monotonic()
        self._background: BackgroundRuntime | None = None if headless else BackgroundRuntime(self)
        self._read_actions = _build_read_actions()
        self._write_actions = _build_write_actions()
        Controller.instance = self
        if self._background is not None:
            self._background.start()
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

    def get_setting(self, scope: str, key: str, default: Any = None) -> Any:
        with self.db() as conn:
            return get_app_setting(conn, scope, key, default)

    def set_setting(self, scope: str, key: str, value: Any) -> None:
        self.record_user_activity()
        with self.db() as conn:
            set_app_setting(conn, scope, key, value)
            conn.commit()
        self.pubsub.pub("settings.changed", scope=scope, key=key, value=value)
        if scope == "system" and key.startswith("background_"):
            self.wake_background_processing()

    def list_settings(self, scope: str | None = None) -> dict[str, dict[str, Any]]:
        with self.db() as conn:
            return list_app_settings(conn, scope)

    def write(self, action: str, *args: Any, **kwargs: Any) -> Any:
        self.record_user_activity()
        fn = self._write_actions.get(action)
        if fn is None:
            raise KeyError(f"unknown write action: {action}")
        with self.db() as conn:
            result = fn(conn, self.db_path, *args, **kwargs)
        self.pubsub.pub(f"data.{action}", result=result)
        self.pubsub.pub("data.changed", action=action, result=result)
        return result

    def record_user_activity(self) -> None:
        self._last_user_activity = time.monotonic()

    def currently_idle(self) -> bool:
        if self._headless:
            return True
        threshold = int(self.get_setting("system", "background_idle_threshold_seconds", 20) or 20)
        return (time.monotonic() - self._last_user_activity) >= max(1, threshold)

    def wake_background_processing(self) -> None:
        if self._background is not None:
            self._background.wake()

    def submit_background_job(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        source: str = "gui",
        dedupe_key: str | None = None,
        priority: int = 100,
        run_after: int | None = None,
    ) -> dict[str, Any]:
        self.record_user_activity()
        if self._background is None:
            raise RuntimeError("background runtime is not available in headless mode")
        return self._background.enqueue(
            job_type,
            payload,
            source=source,
            dedupe_key=dedupe_key,
            priority=priority,
            run_after=run_after,
        )

    def list_background_jobs(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        job_type: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[Any]:
        if self._background is not None:
            return self._background.list_jobs(statuses=statuses, job_type=job_type, source=source, limit=limit)
        with self.db() as conn:
            return list_background_jobs(conn, statuses=statuses, job_type=job_type, source=source, limit=limit)

    def fetch_background_job(self, job_id: int) -> Any | None:
        if self._background is not None:
            return self._background.get_job(int(job_id))
        with self.db() as conn:
            return fetch_background_job(conn, int(job_id))

    def list_background_job_events(self, job_id: int) -> list[Any]:
        if self._background is not None:
            return self._background.list_job_events(int(job_id))
        with self.db() as conn:
            return list_background_job_events(conn, int(job_id))

    def live_job_snapshots(self) -> dict[int, Any]:
        if self._background is None:
            return {}
        return self._background.live_snapshots()

    def cancel_background_job(self, job_id: int) -> bool:
        self.record_user_activity()
        if self._background is not None:
            return self._background.cancel_job(int(job_id))
        with self.db() as conn:
            if cancel_queued_background_job(conn, int(job_id)):
                conn.commit()
                return True
            if cancel_running_background_job(conn, int(job_id)):
                conn.commit()
                return True
            return False

    def terminate_background_job(self, job_id: int) -> bool:
        self.record_user_activity()
        if self._background is not None:
            return self._background.terminate_job(int(job_id))
        with self.db() as conn:
            ok = request_terminate_background_job(conn, int(job_id))
            if ok:
                conn.commit()
            return ok

    def retry_background_job(self, job_id: int) -> bool:
        self.record_user_activity()
        if self._background is not None:
            return self._background.retry_job(int(job_id))
        with self.db() as conn:
            ok = retry_background_job(conn, int(job_id))
            if ok:
                conn.commit()
            return ok

    def purge_background_jobs(self, *, job_ids: list[int] | None = None) -> int:
        self.record_user_activity()
        if self._background is None:
            return 0
        return self._background.purge_jobs(job_ids=job_ids)

    def shutdown(self) -> None:
        LOGGER.info("Controller shutdown begin")
        try:
            if self._background is not None:
                self._background.shutdown()
        except Exception as exc:
            LOGGER.warning("Background runtime shutdown failed: %s", exc)
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
        # per-creator maintenance reads
        "creator_urls": list_creator_urls,
        "creator_aliases": list_creator_aliases,
        "creator_posts": list_creator_posts,
        "creator_reminder": get_creator_reminder,
        "creator_worklogs": list_creator_worklogs,
        "post_meta_history": list_post_meta_history,
        "post_meta_history_detail": fetch_post_meta_history_row,
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
        # update / delete
        "update_url": update_url_record,
        "delete_url": delete_url_record,
        "update_alias": update_alias_record,
        "delete_alias": delete_alias_record,
        "update_post": update_post_record,
        "delete_post": delete_post_record,
        "update_work": update_work_record,
        "delete_work": delete_work_record,
        "update_reminder": update_reminder_record,
        "delete_reminder": delete_reminder_record,
        "refetch_post_meta": refetch_post_meta_record,
        "delete_post_meta_history": delete_post_meta_history_record,
    }
