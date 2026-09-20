"""工作进程入口：只在这里调用引擎。

引擎在资源下载失败时会调用 ``exit(1)``，因此整条引擎调用链必须与界面进程隔离。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import traceback
from typing import Any

from babeldoc_workbench.engine.adapter import run_job
from babeldoc_workbench.engine.protocol import (
    ERROR_ENGINE_EXIT,
    ERROR_WORKER_CRASH,
    EngineApiConfig,
    EngineEvent,
    EngineJobRequest,
    engine_error,
)

logger = logging.getLogger(__name__)

MSG_EVENT = "event"
MSG_CONTROL = "control"
MSG_EXIT = "exit"
# 控制消息格式：{"type": MSG_CONTROL, "action": CONTROL_CANCEL}
CONTROL_CANCEL = "cancel"

CONTROL_POLL_INTERVAL = 0.2


def worker_entry(conn, request_dict: dict[str, Any], api_dict: dict[str, Any]) -> int:
    """工作进程入口函数。

    ``conn`` 是 ``multiprocessing.Pipe`` 的双工连接：主进程发送控制消息，工作进程
    回传事件与退出消息。工作进程不做任何 UI 或数据库写入。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[worker] %(levelname)s %(name)s: %(message)s",
    )
    cancel_event = threading.Event()
    reader_stop = threading.Event()

    def _read_control() -> None:
        while not reader_stop.is_set():
            try:
                if not conn.poll(CONTROL_POLL_INTERVAL):
                    continue
                message = conn.recv()
            except (EOFError, OSError):
                return
            if (
                isinstance(message, dict)
                and message.get("type") == MSG_CONTROL
                and message.get("action") == CONTROL_CANCEL
            ):
                logger.info("收到取消请求")
                cancel_event.set()

    reader = threading.Thread(target=_read_control, name="worker-control", daemon=True)
    reader.start()

    def emit(event: EngineEvent) -> None:
        try:
            conn.send({"type": MSG_EVENT, "event": event.to_dict()})
        except (BrokenPipeError, OSError, ValueError):
            logger.warning("管道已关闭，事件被丢弃：%s", event.type)

    code = 0
    try:
        request = EngineJobRequest.from_dict(request_dict)
        api = EngineApiConfig.from_dict(api_dict)
        state = asyncio.run(run_job(request, api, emit, cancel_event=cancel_event))
        if state.cancelled:
            code = 0
        elif state.finished:
            code = 0
        else:
            code = 1
    except SystemExit as exc:
        # 引擎资源检查失败时会直接退出进程；这里回传结构化提示后再按原退出码退出
        code = exc.code if isinstance(exc.code, int) else 1
        emit(
            engine_error(
                ERROR_ENGINE_EXIT,
                f"引擎进程主动退出（退出码 {code}），通常是资源下载或校验失败",
                error_type="SystemExit",
                hint="请在设置中检查模型、字体与 tiktoken 资源，或导入离线资源包后重试",
            )
        )
        logger.error("引擎调用 exit()，退出码 %s", code)
        raise
    except BaseException as exc:  # noqa: BLE001 - 保证异常不会静默丢失
        code = 1
        logger.exception("工作进程异常")
        emit(
            engine_error(
                ERROR_WORKER_CRASH,
                str(exc) or type(exc).__name__,
                error_type=type(exc).__name__,
                detail=traceback.format_exc(limit=8),
            )
        )
    finally:
        reader_stop.set()
        try:
            conn.send({"type": MSG_EXIT, "code": code})
        except (BrokenPipeError, OSError, ValueError):
            pass
        try:
            conn.close()
        except OSError:
            pass
    return code
