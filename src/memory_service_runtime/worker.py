"""Single-process PostgreSQL worker with leases and graceful shutdown."""
from __future__ import annotations

import logging
import signal
import threading
from typing import Callable

import psycopg

from memory_service_runtime.config import RuntimeConfig
from memory_service_runtime.governed import db
from memory_service_runtime.handlers import TaskExecutionError, TaskHandler, default_handlers
from memory_service_runtime.repository import (
    RuntimeTask,
    claim_task,
    fail_task,
    record_worker_heartbeat,
    recover_expired_tasks,
    succeed_task,
)


LOGGER = logging.getLogger("tkos.memory.worker")


class RuntimeWorker:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        handlers: dict[str, TaskHandler] | None = None,
        connect: Callable[[], psycopg.Connection] | None = None,
    ) -> None:
        self.config = config
        self.handlers = default_handlers() if handlers is None else handlers
        self._connect = connect or (
            lambda: psycopg.connect(
                config.database_url,
                connect_timeout=config.db_connect_timeout,
                application_name=f"tkos-memory-worker:{config.worker_id}",
            )
        )

    def _retry_delay(self, task: RuntimeTask) -> int:
        delay = self.config.retry_base_seconds * (2 ** max(task.attempt - 1, 0))
        return min(delay, self.config.retry_max_seconds)

    def _heartbeat(self, conn: psycopg.Connection, status: str, task_id: str | None = None) -> None:
        record_worker_heartbeat(
            conn,
            tenant_id=self.config.tenant_id,
            organization_id=self.config.organization_id,
            worker_id=self.config.worker_id,
            status=status,
            current_task_id=task_id,
            metadata={"runtime": "postgres-v1"},
        )

    def run_once(self) -> bool:
        """Claim and finish at most one task; all external work runs outside a DB transaction."""
        with self._connect() as conn:
            # First statement of the claim transaction: recover_expired_tasks
            # may UPDATE expired governance.dispatch rows, which 0018 fences
            # behind the runtime capability.
            db.set_write_capability(conn)
            recovery = recover_expired_tasks(
                conn,
                tenant_id=self.config.tenant_id,
                organization_id=self.config.organization_id,
            )
            if recovery.retryable or recovery.failed:
                LOGGER.info(
                    "recovered expired runtime tasks retryable=%d failed=%d",
                    recovery.retryable,
                    recovery.failed,
                )
            task = claim_task(
                conn,
                tenant_id=self.config.tenant_id,
                organization_id=self.config.organization_id,
                worker_id=self.config.worker_id,
                lease_seconds=self.config.lease_seconds,
            )
            self._heartbeat(
                conn,
                "running",
                None if task is None else task.task_id,
            )

        if task is None:
            return False

        handler = self.handlers.get(task.task_type)
        result: dict[str, object] | None = None
        error_code: str | None = None
        retryable = False
        if handler is None:
            error_code = "unsupported_task_type"
        else:
            try:
                result = handler(task)
                if not isinstance(result, dict):
                    raise TaskExecutionError("handler_result_invalid", retryable=False)
            except TaskExecutionError as exc:
                error_code = exc.code
                retryable = exc.retryable
            except Exception:  # task payload and exception text must not enter durable state/log line
                LOGGER.exception(
                    "runtime task handler raised task_id=%s task_type=%s",
                    task.task_id,
                    task.task_type,
                )
                error_code = "handler_unexpected_error"
                retryable = True

        with self._connect() as conn:
            # Independent finalize transaction: re-declare the capability.
            db.set_write_capability(conn)
            if error_code is None and result is not None:
                finalized = succeed_task(conn, task, result=result)
                target = "succeeded" if finalized else None
            else:
                target = fail_task(
                    conn,
                    task,
                    error_code=error_code or "runtime_task_failed",
                    retryable=retryable,
                    retry_delay_seconds=self._retry_delay(task),
                )
            self._heartbeat(conn, "running")

        if target is None:
            LOGGER.warning(
                "stale runtime task lease rejected task_id=%s worker_id=%s",
                task.task_id,
                self.config.worker_id,
            )
        else:
            LOGGER.info(
                "runtime task finalized task_id=%s task_type=%s state=%s attempt=%d",
                task.task_id,
                task.task_type,
                target,
                task.attempt,
            )
        return True

    def run_forever(self, stop: threading.Event) -> None:
        with self._connect() as conn:
            self._heartbeat(conn, "starting")
            self._heartbeat(conn, "running")
        LOGGER.info("runtime worker started worker_id=%s", self.config.worker_id)
        try:
            while not stop.is_set():
                try:
                    processed = self.run_once()
                except (psycopg.Error, OSError):
                    LOGGER.exception("runtime worker cycle failed worker_id=%s", self.config.worker_id)
                    processed = False
                if not processed:
                    stop.wait(self.config.poll_seconds)
        finally:
            try:
                with self._connect() as conn:
                    self._heartbeat(conn, "stopping")
            except (psycopg.Error, OSError):
                LOGGER.warning(
                    "runtime worker could not persist stopping heartbeat worker_id=%s",
                    self.config.worker_id,
                )
            LOGGER.info("runtime worker stopped worker_id=%s", self.config.worker_id)


def install_signal_handlers(stop: threading.Event) -> None:
    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("runtime worker received signal=%d", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
