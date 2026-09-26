"""任务队列：单消费者顺序执行、事件落库与推送、取消与成果索引。

首版按文档顺序执行：同一时刻只有一个任务在运行；文档内部的并发由参数控制。
"""

from __future__ import annotations

import json
import logging
import os
import queue as queue_module
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from babeldoc_workbench.engine.protocol import (
    PROGRESS_EVENT_TYPES,
    EngineApiConfig,
    EngineEvent,
    EngineJobRequest,
)
from babeldoc_workbench.models import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    Task,
)
from babeldoc_workbench.services import (
    api_profiles,
    glossaries,
    lifecycle,
    task_store,
    worker_registry,
)
from babeldoc_workbench.services.params import to_engine_fields
from babeldoc_workbench.services.worker_runner import (
    DEFAULT_CANCEL_TIMEOUT,
    run_worker_process,
)
from babeldoc_workbench.settings import current_paths

logger = logging.getLogger(__name__)

TASK_SUBDIRS = ("input", "work", "output", "logs")
SKIP_TRANSLATION_ENV = "BABELDOC_ALLOW_SKIP_TRANSLATION"
SKIP_TRANSLATION_KEY = "skip_translation"
USER_OUTPUT_DIR_KEY = "_user_output_dir"
USER_OUTPUT_DIRS_KEY = "_user_output_dirs"


def allow_skip_translation() -> bool:
    """仅用于自动验证：默认关闭，避免把“跳过翻译”当成正式功能。"""
    return os.environ.get(SKIP_TRANSLATION_ENV) == "1"


@dataclass
class QueueConfig:
    poll_interval: float = 0.5
    db_event_interval: float = 1.0
    sse_event_interval: float = 0.25
    event_retention: int = task_store.EVENT_RETENTION
    cancel_timeout: float = DEFAULT_CANCEL_TIMEOUT
    subscriber_queue_size: int = 500


def task_dirs(task_id: int) -> dict[str, Path]:
    root = current_paths().tasks / str(task_id)
    return {name: root / name for name in TASK_SUBDIRS} | {"root": root}


def ensure_task_dirs(task_id: int) -> dict[str, Path]:
    dirs = task_dirs(task_id)
    for name in TASK_SUBDIRS:
        dirs[name].mkdir(parents=True, exist_ok=True)
    return dirs


@dataclass
class TaskQueue:
    """单消费者任务队列。"""

    runner: Callable[..., Any] = run_worker_process
    config: QueueConfig = field(default_factory=QueueConfig)
    _thread: threading.Thread | None = None
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _wake_event: threading.Event = field(default_factory=threading.Event)
    _cancel_events: dict[int, threading.Event] = field(default_factory=dict)
    _subscribers: dict[int, set[queue_module.Queue]] = field(default_factory=dict)
    _subscriber_lock: threading.Lock = field(default_factory=threading.Lock)
    _current_task_id: int | None = None

    # --- 生命周期 -------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="babeldoc-task-queue", daemon=True
        )
        self._thread.start()
        logger.info("任务队列已启动")

    def stop(self, timeout: float = 20.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        current = self._current_task_id
        if current is not None:
            event = self._cancel_events.get(current)
            if event is not None:
                event.set()
        if self._thread:
            self._thread.join(timeout)

    # --- 对外操作 -------------------------------------------------------
    def submit(self, task_id: int) -> None:
        self._wake_event.set()
        logger.info("任务入队：%s", task_id)

    def cancel(self, task_id: int) -> dict[str, Any]:
        task = task_store.get_task(task_id)
        if task.status == TASK_STATUS_QUEUED:
            task_store.mark_cancelled(task)
            self._append_event(
                task,
                "cancelled",
                {"code": "cancelled_by_user", "message": "任务在排队时被取消"},
            )
            self._publish(task.id, EngineEvent.of("cancelled", code="cancelled_by_user"))
            return {"task_id": task_id, "status": TASK_STATUS_CANCELLED}
        event = self._cancel_events.get(task_id)
        if event is not None and not event.is_set():
            event.set()
            task.cancel_requested = True
            task.save()
            logger.info("已请求取消任务：%s", task_id)
            return {"task_id": task_id, "status": "cancelling"}
        return {
            "task_id": task_id,
            "status": task.status,
            "message": "任务已结束或未在运行",
        }

    def current_task_id(self) -> int | None:
        return self._current_task_id

    def subscribe(self, task_id: int) -> queue_module.Queue:
        subscriber: queue_module.Queue = queue_module.Queue(
            maxsize=self.config.subscriber_queue_size
        )
        with self._subscriber_lock:
            self._subscribers.setdefault(task_id, set()).add(subscriber)
        return subscriber

    def unsubscribe(self, task_id: int, subscriber: queue_module.Queue) -> None:
        with self._subscriber_lock:
            subscribers = self._subscribers.get(task_id)
            if subscribers and subscriber in subscribers:
                subscribers.discard(subscriber)
            if subscribers is not None and not subscribers:
                self._subscribers.pop(task_id, None)

    # --- 队列主循环 -----------------------------------------------------
    def _loop(self) -> None:
        while not self._stop_event.is_set():
            task = task_store.claim_next_queued()
            if task is None:
                self._wake_event.wait(self.config.poll_interval)
                self._wake_event.clear()
                continue
            self._current_task_id = task.id
            try:
                self._run_task(task)
            except Exception as exc:  # noqa: BLE001 - 队列不能因单个任务退出
                logger.exception("任务执行失败：%s", task.id)
                task_store.mark_failed(
                    task, code="queue_error", message=f"{type(exc).__name__}: {exc}"
                )
                self._append_event(
                    task,
                    "error",
                    {"code": "queue_error", "message": str(exc)},
                )
            finally:
                self._current_task_id = None
                self._cancel_events.pop(task.id, None)

    def _run_task(self, task: Task) -> None:
        snapshot = task_store.params_snapshot(task)
        dirs = ensure_task_dirs(task.id)
        skip_translation = bool(
            snapshot.get(SKIP_TRANSLATION_KEY)
        ) and allow_skip_translation()

        api: EngineApiConfig | None = (
            EngineApiConfig(model="offline-stub") if skip_translation else None
        )
        if api is None:
            if not task.api_profile_id:
                task_store.mark_failed(
                    task, code="missing_api_profile", message="任务没有绑定 API 配置"
                )
                return
            try:
                api = api_profiles.resolve_api_config(task.api_profile_id)
            except api_profiles.ProfileError as exc:
                task_store.mark_failed(
                    task, code="missing_api_profile", message=str(exc)
                )
                return
            if not api.api_key:
                task_store.mark_failed(
                    task,
                    code="missing_api_key",
                    message="该 API 配置尚未保存 API Key，请在设置中补充后重新执行",
                )
                return

        fields = to_engine_fields(snapshot)
        glossary_files: tuple[str, ...] = ()
        if task.glossary_version_id:
            try:
                version, _glossary = glossaries.version_for_task(task.glossary_version_id)
            except glossaries.GlossaryError as exc:
                task_store.mark_failed(
                    task, code="glossary_unavailable", message=str(exc)
                )
                return
            glossary_files = (str(version.snapshot_path),)
        request = EngineJobRequest(
            job_id=f"task-{task.id}",
            input_path=task.stored_input_path,
            output_dir=str(dirs["output"]),
            **fields,
            glossary_files=glossary_files,
            skip_translation=skip_translation,
        )
        task_store.mark_running(task)
        self._append_event(task, "status", {"status": TASK_STATUS_RUNNING})
        self._publish(task.id, EngineEvent.of("status", status=TASK_STATUS_RUNNING))

        cancel_event = threading.Event()
        self._cancel_events[task.id] = cancel_event
        if task.cancel_requested:
            cancel_event.set()

        state = {"last_db": 0.0, "last_sse": 0.0}

        def on_event(event: dict[str, Any]) -> None:
            self._handle_event(task, event, state)

        summary = self.runner(
            request_dict=request.to_dict(),
            api_dict=api.to_dict(),
            on_event=on_event,
            cancel_event=cancel_event,
            cancel_timeout=self.config.cancel_timeout,
            process_name=f"babeldoc-task-{task.id}",
            on_started=worker_registry.register,
            on_finished=worker_registry.unregister,
        )

        if summary.progress is not None:
            task.progress = float(summary.progress)
        if summary.stage:
            task.stage = summary.stage

        if summary.cancelled:
            self._discard_incomplete_outputs(dirs)
            task_store.refresh_outputs(task)
            task_store.mark_cancelled(task)
            if summary.terminate_failed:
                task_store.append_event(
                    task,
                    "warning",
                    {"message": "取消超时后强制终止进程失败，请在任务管理器中确认"},
                )
            self._append_event(
                task,
                "cancelled",
                {"code": "cancelled_by_user", "message": "任务已取消"},
            )
            self._publish(task.id, EngineEvent.of("cancelled", code="cancelled_by_user"))
            return

        outputs = task_store.refresh_outputs(task)
        produced = [
            item for item in outputs if item["kind"] in {"mono", "dual"} and item["exists"]
        ]
        if summary.finished and produced:
            user_output_dirs = snapshot.get(USER_OUTPUT_DIRS_KEY)
            if not isinstance(user_output_dirs, dict) and snapshot.get(USER_OUTPUT_DIR_KEY):
                user_output_dirs = {
                    kind: snapshot[USER_OUTPUT_DIR_KEY] for kind in lifecycle.RESULT_KINDS
                }
            if user_output_dirs:
                try:
                    exported = lifecycle.export_copies(
                        task, outputs,
                        {kind: Path(path) for kind, path in user_output_dirs.items()
                         if kind in lifecycle.RESULT_KINDS and isinstance(path, str)},
                    )
                except Exception as exc:  # noqa: BLE001 - 导出失败不得把已完成的翻译改成失败
                    logger.exception("任务 %s 的默认目录导出失败", task.id)
                    exported = {
                        "items": [],
                        "errors": [f"导出失败：{type(exc).__name__}: {exc}"],
                    }
                self._append_event(task, "exported", exported)
            task.engine_version = task.engine_version or _engine_version()
            task_store.mark_succeeded(task)
            self._append_event(
                task,
                "status",
                {
                    "status": "succeeded",
                    "outputs": [item["path"] for item in outputs],
                },
            )
            return

        error = summary.error or {}
        if summary.finished and not produced:
            code = "no_output"
            message = "引擎报告完成，但没有找到可用成果文件"
        elif error:
            code = str(error.get("code") or "engine_failure")
            message = str(error.get("message") or "引擎执行失败")
            if error.get("hint"):
                message = f"{message}（{error['hint']}）"
        else:
            code = (
                "worker_crashed" if summary.exit_code not in (0, None) else "engine_stopped"
            )
            message = (
                f"工作进程异常退出（退出码 {summary.exit_code}），通常是资源下载或环境问题"
                if summary.exit_code not in (0, None)
                else "工作进程在没有报告完成的情况下结束"
            )
        task_store.mark_failed(task, code=code, message=message)
        self._append_event(
            task,
            "status",
            {"status": "failed", "error_code": code, "error_message": message},
        )

    def _handle_event(
        self, task: Task, event: dict[str, Any], state: dict[str, float]
    ) -> None:
        event_type = str(event.get("type") or "")
        payload = event.get("payload") or {}
        now = time.monotonic()
        is_progress = event_type in PROGRESS_EVENT_TYPES
        if is_progress:
            if isinstance(payload.get("overall_progress"), (int, float)):
                task.progress = float(payload["overall_progress"])
            if payload.get("stage"):
                task.stage = str(payload["stage"])
            if now - state["last_sse"] >= self.config.sse_event_interval:
                state["last_sse"] = now
                self._publish(task.id, EngineEvent(type=event_type, payload=payload))
            if now - state["last_db"] >= self.config.db_event_interval:
                state["last_db"] = now
                task.save()
                self._append_event(task, event_type, payload)
            return
        task.save()
        self._append_event(task, event_type, payload)
        self._publish(task.id, EngineEvent(type=event_type, payload=payload))

    def _append_event(
        self, task: Task, event_type: str, payload: dict[str, Any]
    ) -> None:
        task_store.append_event(
            task, event_type, payload, keep=self.config.event_retention
        )
        self._write_event_log(task, event_type, payload)

    def _write_event_log(
        self, task: Task, event_type: str, payload: dict[str, Any]
    ) -> None:
        text = str(task.log_dir or "").strip()
        if not text or text == ".":
            logger.warning("任务 %s 的日志目录未就绪，跳过事件日志写入", task.id)
            return
        log_dir = Path(text)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "type": event_type,
                    "payload": payload,
                },
                ensure_ascii=False,
            )
            with (log_dir / task_store.EVENT_LOG_NAME).open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write(line + "\n")
        except OSError as exc:  # pragma: no cover - 磁盘异常
            logger.warning("写入任务事件日志失败：%s", exc)

    def _publish(self, task_id: int, event: EngineEvent) -> None:
        with self._subscriber_lock:
            subscribers = list(self._subscribers.get(task_id, ()))
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event.to_dict())
            except queue_module.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(event.to_dict())
                except queue_module.Empty:  # pragma: no cover
                    pass

    def _discard_incomplete_outputs(self, dirs: dict[str, Path]) -> None:
        """取消后清理未完成的输出与工作目录，避免把半成品当成成果。"""
        shutil.rmtree(dirs["work"], ignore_errors=True)
        output_dir = dirs["output"]
        if output_dir.is_dir():
            for item in output_dir.iterdir():
                if item.is_file():
                    try:
                        item.unlink()
                    except OSError as exc:  # pragma: no cover
                        logger.warning("删除未完成输出失败：%s（%s）", item, exc)


def _engine_version() -> str:
    try:
        from babeldoc.const import __version__ as version

        return str(version)
    except Exception:  # pragma: no cover
        return "unknown"
