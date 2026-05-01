from __future__ import annotations

import threading
import time
import traceback
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from db import (
    cancel_queued_background_job,
    cancel_running_background_job,
    claim_background_job,
    complete_background_job,
    connect,
    emit_app_event,
    fail_background_job,
    fetch_background_job,
    finish_cancelled_background_job,
    get_app_setting,
    get_latest_app_event_id,
    heartbeat_background_job,
    list_app_events_since,
    list_background_job_events,
    list_background_jobs,
    list_runnable_background_jobs,
    purge_background_jobs,
    recover_stale_background_jobs,
    requeue_background_job_after_retryable_error,
    request_terminate_background_job,
    retry_background_job,
)
from .background import (
    BACKGROUND_JOB_CANCELLED,
    BACKGROUND_JOB_QUEUED,
    BACKGROUND_JOB_RUNNING,
    BACKGROUND_JOB_SUCCEEDED,
    DEFAULT_SLOT_LIMITS,
    BackgroundCancelledError,
    LiveJobStatus,
    RetryableBackgroundError,
)
from .background_handlers import BackgroundJobContext, get_handler, run_background_job
from .config import LOGGER
from .constants import ConflictError, UserError
from .utils import json_loads

if TYPE_CHECKING:
    from .controller import Controller


LEASE_SECONDS = 30
HEARTBEAT_SECONDS = 5


@dataclass(frozen=True)
class HandlerPolicy:
    max_attempts: int
    timeout_seconds: int


class BackgroundEnqueueApi:
    def __init__(self, db_path: Path, controller: "Controller | None" = None) -> None:
        self._db_path = db_path
        self._controller = controller

    def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        source: str = "gui",
        dedupe_key: str | None = None,
        priority: int = 100,
        run_after: int | None = None,
    ) -> dict[str, Any]:
        handler = get_handler(job_type)
        with closing(connect(self._db_path)) as conn:
            policy = _handler_policy(conn, job_type)
            from db import enqueue_background_job

            receipt = enqueue_background_job(
                conn,
                job_type,
                handler.title_builder(payload),
                payload,
                source=source,
                slot_key=handler.slot_key,
                dedupe_key=dedupe_key,
                priority=priority,
                max_attempts=policy.max_attempts,
                run_after=run_after,
            )
            conn.commit()
        if self._controller is not None:
            self._controller.pubsub.pub("queue.updated", job_id=receipt["job_id"])
            self._controller.wake_background_processing()
        return receipt


class ManagerWithMainLoop:
    def __init__(self, runtime: "BackgroundRuntime", name: str) -> None:
        self._runtime = runtime
        self._name = name
        self._wake_event = threading.Event()
        self._shutdown = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"clog-{name}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def shutdown(self, wait: bool = True) -> None:
        self._shutdown.set()
        self._wake_event.set()
        if wait and self._thread.is_alive():
            self._thread.join(timeout=5)

    def wake(self) -> None:
        self._wake_event.set()

    def _run(self) -> None:
        while not self._shutdown.is_set():
            self._wake_event.clear()
            wait_seconds = 0.5
            try:
                wait_seconds = max(0.05, float(self._do_main_loop()))
            except Exception as exc:
                LOGGER.exception("%s main loop failed: %s", self._name, exc)
                wait_seconds = 1.0
            self._wake_event.wait(wait_seconds)

    def _do_main_loop(self) -> float:
        raise NotImplementedError


class QueuedExternalWorkManager(ManagerWithMainLoop):
    def __init__(self, runtime: "BackgroundRuntime") -> None:
        super().__init__(runtime, "background-queue")

    def _do_main_loop(self) -> float:
        mode = self._runtime.processing_mode()
        if not self._runtime.background_work_allowed(mode):
            return self._runtime.poll_interval_seconds()
        dispatched = self._runtime.dispatch_packet(mode)
        if dispatched <= 0:
            return self._runtime.poll_interval_seconds()
        return self._runtime.rest_seconds(mode, dispatched)


class BackgroundEventRelayManager(ManagerWithMainLoop):
    def __init__(self, runtime: "BackgroundRuntime", *, last_event_id: int) -> None:
        super().__init__(runtime, "background-events")
        self._last_event_id = int(last_event_id)

    def _do_main_loop(self) -> float:
        with closing(connect(self._runtime.db_path)) as conn:
            rows = list_app_events_since(conn, self._last_event_id, limit=200)
        for row in rows:
            self._last_event_id = int(row["id"])
            self._runtime.publish_app_event(str(row["topic"]), json_loads(row["payload_json"], None))
        return 0.5


class BackgroundRuntime:
    def __init__(self, controller: "Controller") -> None:
        self._controller = controller
        self.db_path = controller.db_path
        self._worker_id = f"gui-{uuid.uuid4().hex[:8]}"
        self._enqueue_api = BackgroundEnqueueApi(self.db_path, controller)
        self._lock = threading.RLock()
        self._live_statuses: dict[int, LiveJobStatus] = {}
        self._running_slots: dict[int, str] = {}
        self._heartbeat_stops: dict[int, threading.Event] = {}
        self._worker_threads: dict[int, threading.Thread] = {}
        with closing(connect(self.db_path)) as conn:
            last_event_id = get_latest_app_event_id(conn)
            recovered = recover_stale_background_jobs(conn)
            conn.commit()
        if recovered:
            controller.pubsub.pub("queue.updated", recovered=recovered)
        self._queue_manager = QueuedExternalWorkManager(self)
        self._event_manager = BackgroundEventRelayManager(self, last_event_id=last_event_id)

    def start(self) -> None:
        self._event_manager.start()
        self._queue_manager.start()

    def shutdown(self) -> None:
        self._queue_manager.shutdown(wait=True)
        self._event_manager.shutdown(wait=True)
        with self._lock:
            statuses = list(self._live_statuses.values())
        for status in statuses:
            status.request_terminate()
        deadline = time.time() + 5
        while time.time() < deadline:
            with self._lock:
                if not self._worker_threads:
                    break
                threads = list(self._worker_threads.values())
            for thread in threads:
                thread.join(timeout=0.1)

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def wake(self) -> None:
        self._queue_manager.wake()

    def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        source: str = "gui",
        dedupe_key: str | None = None,
        priority: int = 100,
        run_after: int | None = None,
    ) -> dict[str, Any]:
        receipt = self._enqueue_api.enqueue(
            job_type,
            payload,
            source=source,
            dedupe_key=dedupe_key,
            priority=priority,
            run_after=run_after,
        )
        self.wake()
        return receipt

    def list_jobs(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        job_type: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[Any]:
        with closing(connect(self.db_path)) as conn:
            return list_background_jobs(
                conn,
                statuses=statuses,
                job_type=job_type,
                source=source,
                limit=limit,
            )

    def get_job(self, job_id: int) -> Any | None:
        with closing(connect(self.db_path)) as conn:
            return fetch_background_job(conn, int(job_id))

    def list_job_events(self, job_id: int) -> list[Any]:
        with closing(connect(self.db_path)) as conn:
            return list_background_job_events(conn, int(job_id))

    def live_snapshots(self) -> dict[int, Any]:
        with self._lock:
            return {job_id: status.snapshot() for job_id, status in self._live_statuses.items()}

    def cancel_job(self, job_id: int) -> bool:
        with closing(connect(self.db_path)) as conn:
            if cancel_queued_background_job(conn, int(job_id)):
                conn.commit()
                self._controller.pubsub.pub("queue.updated", job_id=int(job_id))
                return True
            if cancel_running_background_job(conn, int(job_id)):
                conn.commit()
                self._controller.pubsub.pub("queue.updated", job_id=int(job_id))
                self._request_live_cancel(int(job_id))
                return True
            return False

    def terminate_job(self, job_id: int) -> bool:
        with closing(connect(self.db_path)) as conn:
            if not request_terminate_background_job(conn, int(job_id)):
                return False
            conn.commit()
        self._controller.pubsub.pub("queue.updated", job_id=int(job_id))
        self._request_live_terminate(int(job_id))
        return True

    def retry_job(self, job_id: int) -> bool:
        with closing(connect(self.db_path)) as conn:
            ok = retry_background_job(conn, int(job_id))
            if ok:
                conn.commit()
        if ok:
            self._controller.pubsub.pub("queue.updated", job_id=int(job_id))
            self.wake()
        return ok

    def purge_jobs(self, *, job_ids: list[int] | None = None) -> int:
        with closing(connect(self.db_path)) as conn:
            count = purge_background_jobs(conn, job_ids=job_ids)
            if count:
                conn.commit()
        if count:
            self._controller.pubsub.pub("queue.updated", purged=count)
        return count

    def publish_app_event(self, topic: str, payload: Any) -> None:
        if isinstance(payload, dict):
            self._controller.pubsub.pub(topic, **payload)
        else:
            self._controller.pubsub.pub(topic, payload=payload)

    def processing_mode(self) -> str:
        if self._controller._headless:
            return "idle"
        with closing(connect(self.db_path)) as conn:
            threshold = int(get_app_setting(conn, "system", "background_idle_threshold_seconds", 20) or 20)
        return "idle" if (time.monotonic() - self._controller._last_user_activity) >= max(1, threshold) else "active"

    def background_work_allowed(self, mode: str) -> bool:
        key = "background_work_during_idle" if mode == "idle" else "background_work_during_active"
        with closing(connect(self.db_path)) as conn:
            return bool(get_app_setting(conn, "system", key, True))

    def poll_interval_seconds(self) -> float:
        with closing(connect(self.db_path)) as conn:
            interval_ms = int(get_app_setting(conn, "system", "background_queue_poll_interval_ms", 1000) or 1000)
        return max(0.1, interval_ms / 1000.0)

    def rest_seconds(self, mode: str, dispatched: int) -> float:
        packet_ms_key = "background_idle_packet_ms" if mode == "idle" else "background_active_packet_ms"
        rest_key = "background_idle_rest_percentage" if mode == "idle" else "background_active_rest_percentage"
        with closing(connect(self.db_path)) as conn:
            packet_ms = int(get_app_setting(conn, "system", packet_ms_key, 1000) or 1000)
            rest_pct = int(get_app_setting(conn, "system", rest_key, 25) or 25)
        base = packet_ms / 1000.0
        return max(0.05, base * (rest_pct / 100.0) / max(1, dispatched))

    def dispatch_packet(self, mode: str) -> int:
        packet_ms_key = "background_idle_packet_ms" if mode == "idle" else "background_active_packet_ms"
        with closing(connect(self.db_path)) as conn:
            packet_ms = int(get_app_setting(conn, "system", packet_ms_key, 1000) or 1000)
        deadline = time.monotonic() + max(0.1, packet_ms / 1000.0)
        dispatched = 0
        while time.monotonic() < deadline:
            if not self._can_dispatch_more():
                break
            claimed = self._claim_next_job()
            if claimed is None:
                break
            self._spawn_worker(claimed)
            dispatched += 1
        return dispatched

    def _can_dispatch_more(self) -> bool:
        with closing(connect(self.db_path)) as conn:
            global_limit = int(get_app_setting(conn, "system", "background_queue_global_concurrency", 2) or 2)
        with self._lock:
            return len(self._worker_threads) < max(1, global_limit)

    def _claim_next_job(self) -> Any | None:
        with closing(connect(self.db_path)) as conn:
            runnable = list_runnable_background_jobs(conn, limit=50)
            slot_limits = _slot_limits(conn)
            active_slots = self._active_slot_counts()
            for row in runnable:
                slot_key = str(row["slot_key"] or "gdl")
                if active_slots.get(slot_key, 0) >= slot_limits.get(slot_key, max(1, len(slot_limits) or 1)):
                    continue
                claimed = claim_background_job(
                    conn,
                    int(row["id"]),
                    worker_id=self._worker_id,
                    lease_seconds=LEASE_SECONDS,
                )
                if claimed is not None:
                    conn.commit()
                    return claimed
        return None

    def _spawn_worker(self, job_row: Any) -> None:
        job_id = int(job_row["id"])
        job_type = str(job_row["job_type"])
        title = str(job_row["title"])
        handler = get_handler(job_type)
        live_status = LiveJobStatus(
            job_id,
            job_type,
            title,
            can_cancel=True,
            can_terminate=handler.supports_terminate,
        )
        live_status.mark_started(title)
        with self._lock:
            self._live_statuses[job_id] = live_status
            self._running_slots[job_id] = handler.slot_key
        heartbeat_stop = threading.Event()
        worker = threading.Thread(
            target=self._run_worker,
            args=(dict(job_row), live_status, heartbeat_stop),
            name=f"clog-job-{job_id}",
            daemon=True,
        )
        with self._lock:
            self._heartbeat_stops[job_id] = heartbeat_stop
            self._worker_threads[job_id] = worker
        self._controller.pubsub.pub("queue.updated", job_id=job_id)
        worker.start()

    def _run_worker(self, job_row: dict[str, Any], live_status: LiveJobStatus, heartbeat_stop: threading.Event) -> None:
        job_id = int(job_row["id"])
        job_type = str(job_row["job_type"])
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(job_id, live_status, heartbeat_stop),
            name=f"clog-heartbeat-{job_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            payload = json_loads(job_row.get("payload_json"), {}) or {}
            with closing(connect(self.db_path)) as conn:
                ctx = BackgroundJobContext(
                    controller=self._controller,
                    conn=conn,
                    db_path=self.db_path,
                    worker_id=self._worker_id,
                    job_status=live_status,
                    enqueue_api=self._enqueue_api,
                )
                with conn:
                    result = run_background_job(ctx, job_type, payload)

            outcome_code = None
            if isinstance(result, dict):
                outcome_code = result.get("outcome_code")
            with closing(connect(self.db_path)) as conn:
                complete_background_job(conn, job_id, result=result, outcome_code=outcome_code)
                if isinstance(result, dict) and result.get("data_changed"):
                    emit_app_event(conn, "data.changed", {"job_id": job_id, "job_type": job_type})
                conn.commit()
            live_status.finish(BACKGROUND_JOB_SUCCEEDED, text="已完成")
            if isinstance(result, dict) and result.get("data_changed"):
                self._controller.pubsub.pub("data.changed", job_id=job_id, job_type=job_type)
        except BackgroundCancelledError as exc:
            with closing(connect(self.db_path)) as conn:
                finish_cancelled_background_job(conn, job_id, error_text=str(exc))
                conn.commit()
            live_status.finish(BACKGROUND_JOB_CANCELLED, text="已取消", error=str(exc))
        except RetryableBackgroundError as exc:
            self._handle_retryable_failure(job_id, job_type, str(exc), live_status)
        except ConflictError as exc:
            self._handle_failed_job(job_id, "conflict", str(exc), live_status)
        except UserError as exc:
            self._handle_failed_job(job_id, "user_error", str(exc), live_status)
        except Exception as exc:
            detail = "".join(traceback.format_exception(exc))
            self._handle_failed_job(job_id, "exception", detail, live_status)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)
            with self._lock:
                self._live_statuses.pop(job_id, None)
                self._running_slots.pop(job_id, None)
                self._heartbeat_stops.pop(job_id, None)
                self._worker_threads.pop(job_id, None)
            self._controller.pubsub.pub("queue.updated", job_id=job_id)
            self.wake()

    def _handle_retryable_failure(self, job_id: int, job_type: str, message: str, live_status: LiveJobStatus) -> None:
        with closing(connect(self.db_path)) as conn:
            row = fetch_background_job(conn, job_id)
            attempt_count = int(row["attempt_count"]) if row is not None else 0
            max_attempts = int(row["max_attempts"]) if row is not None else 1
            if attempt_count < max_attempts:
                requeue_background_job_after_retryable_error(conn, job_id, error_text=message, delay_seconds=30)
                conn.commit()
                live_status.finish(BACKGROUND_JOB_QUEUED, text="已安排稍後重試", error=message)
                return
        self._handle_failed_job(job_id, "retry_exhausted", message, live_status)

    def _handle_failed_job(
        self,
        job_id: int,
        outcome_code: str,
        message: str,
        live_status: LiveJobStatus,
    ) -> None:
        with closing(connect(self.db_path)) as conn:
            fail_background_job(conn, job_id, error_text=message, outcome_code=outcome_code)
            conn.commit()
        live_status.finish("failed", text="執行失敗", error=message)

    def _heartbeat_loop(self, job_id: int, live_status: LiveJobStatus, stop_event: threading.Event) -> None:
        while not stop_event.wait(HEARTBEAT_SECONDS):
            with closing(connect(self.db_path)) as conn:
                ok = heartbeat_background_job(
                    conn,
                    job_id,
                    worker_id=self._worker_id,
                    lease_seconds=LEASE_SECONDS,
                )
                row = fetch_background_job(conn, job_id)
            if not ok or row is None:
                return
            if row["terminate_requested_at"] and not live_status.terminate_requested():
                live_status.request_terminate()
            elif row["cancel_requested_at"] and not live_status.cancel_requested():
                live_status.request_cancel()

    def _active_slot_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for slot_key in self._running_slots.values():
                counts[slot_key] = counts.get(slot_key, 0) + 1
        return counts

    def _request_live_cancel(self, job_id: int) -> None:
        with self._lock:
            status = self._live_statuses.get(int(job_id))
        if status is not None:
            status.request_cancel()

    def _request_live_terminate(self, job_id: int) -> None:
        with self._lock:
            status = self._live_statuses.get(int(job_id))
        if status is not None:
            status.request_terminate()


def _slot_limits(conn: Any) -> dict[str, int]:
    raw = get_app_setting(conn, "system", "background_slot_limits", DEFAULT_SLOT_LIMITS)
    result = dict(DEFAULT_SLOT_LIMITS)
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                result[str(key)] = max(1, int(value))
            except (TypeError, ValueError):
                continue
    return result


def _handler_policy(conn: Any, job_type: str) -> HandlerPolicy:
    handler = get_handler(job_type)
    raw = get_app_setting(conn, "system", "background_handler_limits", {}) or {}
    job_config = raw.get(job_type, {}) if isinstance(raw, dict) else {}
    try:
        max_attempts = max(1, int(job_config.get("max_attempts", handler.max_attempts)))
    except (TypeError, ValueError):
        max_attempts = handler.max_attempts
    try:
        timeout_seconds = max(1, int(job_config.get("timeout_seconds", handler.timeout_seconds)))
    except (TypeError, ValueError):
        timeout_seconds = handler.timeout_seconds
    return HandlerPolicy(max_attempts=max_attempts, timeout_seconds=timeout_seconds)
