"""工作进程运行器：启动 spawn 子进程、转发事件、执行取消与超时兜底。"""

from __future__ import annotations

import logging
import multiprocessing
import time
from dataclasses import dataclass
from typing import Any, Callable

from babeldoc_workbench.engine.protocol import TERMINAL_EVENT_TYPES
from babeldoc_workbench.engine.worker import (
    CONTROL_CANCEL,
    MSG_CONTROL,
    MSG_EVENT,
    MSG_EXIT,
    worker_entry,
)

logger = logging.getLogger(__name__)

DEFAULT_CANCEL_TIMEOUT = 10.0
DEFAULT_TERMINATE_TIMEOUT = 5.0
DEFAULT_POLL_INTERVAL = 0.2


@dataclass
class WorkerSummary:
    """一次工作进程运行的结果摘要。"""

    finished: bool = False
    cancelled: bool = False
    terminate_failed: bool = False
    error: dict[str, Any] | None = None
    finish: dict[str, Any] | None = None
    exit_code: int | None = None
    stage: str | None = None
    progress: float | None = None
    events: int = 0


def run_worker_process(
    *,
    request_dict: dict[str, Any],
    api_dict: dict[str, Any],
    on_event: Callable[[dict[str, Any]], None] | None = None,
    cancel_event=None,
    cancel_timeout: float = DEFAULT_CANCEL_TIMEOUT,
    terminate_timeout: float = DEFAULT_TERMINATE_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    process_name: str = "babeldoc-worker",
) -> WorkerSummary:
    """运行一次任务；``cancel_event`` 置位后先发取消消息，超时再终止进程。"""
    summary = WorkerSummary()
    context = multiprocessing.get_context("spawn")
    parent_conn, child_conn = context.Pipe(duplex=True)
    process = context.Process(
        target=worker_entry,
        args=(child_conn, request_dict, api_dict),
        name=process_name,
    )
    process.start()
    child_conn.close()

    cancel_sent = False
    cancel_deadline = 0.0
    try:
        while True:
            now = time.monotonic()
            if cancel_event is not None and cancel_event.is_set() and not cancel_sent:
                logger.info("向工作进程发送取消请求")
                try:
                    parent_conn.send({"type": MSG_CONTROL, "action": CONTROL_CANCEL})
                except (BrokenPipeError, OSError):
                    pass
                cancel_sent = True
                cancel_deadline = now + cancel_timeout
                summary.cancelled = True
            if cancel_sent and now > cancel_deadline and process.is_alive():
                logger.warning("取消后 %.0f 秒仍未退出，终止工作进程", cancel_timeout)
                process.terminate()
                process.join(terminate_timeout)
                if process.is_alive():
                    summary.terminate_failed = True
                    logger.error("工作进程无法终止，可能需要用户手动结束")
                break

            if parent_conn.poll(poll_interval):
                try:
                    message = parent_conn.recv()
                except EOFError:
                    break
                if isinstance(message, dict):
                    _apply_message(message, summary, on_event)
                    if message.get("type") == MSG_EXIT:
                        break
            elif not process.is_alive():
                break
    finally:
        process.join(terminate_timeout)
        summary.exit_code = process.exitcode
        try:
            parent_conn.close()
        except OSError:
            pass
    if cancel_sent:
        summary.cancelled = True
    return summary


def _apply_message(
    message: dict[str, Any],
    summary: WorkerSummary,
    on_event: Callable[[dict[str, Any]], None] | None,
) -> None:
    message_type = message.get("type")
    if message_type == MSG_EXIT:
        code = message.get("code")
        summary.exit_code = code if isinstance(code, int) else None
        return
    if message_type != MSG_EVENT:
        return
    event = message.get("event") or {}
    event_type = event.get("type")
    payload = event.get("payload") or {}
    summary.events += 1
    if event_type in TERMINAL_EVENT_TYPES:
        if event_type == "finish":
            summary.finished = True
            summary.finish = payload
        elif event_type == "error":
            summary.error = payload
        elif event_type == "cancelled":
            summary.cancelled = True
    if isinstance(payload.get("overall_progress"), (int, float)):
        summary.progress = float(payload["overall_progress"])
    if payload.get("stage"):
        summary.stage = str(payload["stage"])
    if on_event is not None:
        on_event(event)
