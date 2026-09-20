"""重启恢复：遗留任务标记、残留工作进程清理。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from babeldoc_workbench.models import (
    TASK_ACTIVE_STATUSES,
    TASK_STATUS_INTERRUPTED,
    Task,
)
from babeldoc_workbench.services import task_store, worker_registry

logger = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = "应用上次未正常退出，任务已中断；可重新执行"
INTERRUPTED_OUTPUT_NOTE = "可能不完整"


def _possibly_file(proc) -> bool:
    return False


def active_tasks() -> list[Task]:
    return list(Task.select().where(Task.status << list(TASK_ACTIVE_STATUSES)))


def mark_interrupted_tasks() -> list[dict[str, Any]]:
    """把所有仍处于 preparing/running 的任务标记为 ``interrupted``。

    成果文件保留，但索引会被标记为“可能不完整”；只有重新执行成功才会更新索引。
    """
    interrupted: list[dict[str, Any]] = []
    for task in active_tasks():
        task.status = TASK_STATUS_INTERRUPTED
        task.finished_at = datetime.now()
        task.error_code = "interrupted"
        task.error_message = INTERRUPTED_MESSAGE
        task.save()
        task_store.append_event(
            task,
            "status",
            {
                "status": TASK_STATUS_INTERRUPTED,
                "last_stage": task.stage,
                "message": INTERRUPTED_MESSAGE,
                "note": INTERRUPTED_OUTPUT_NOTE,
            },
        )
        outputs = task_store.refresh_outputs(task)
        interrupted.append(
            {
                "task_id": task.id,
                "last_stage": task.stage,
                "outputs": len(outputs),
            }
        )
        logger.warning(
            "任务 %s 上次未正常结束（阶段 %s），已标记为 interrupted",
            task.id,
            task.stage,
        )
    return interrupted


def _same_interpreter(proc, self_exe: str | None, self_name: str | None) -> bool:
    try:
        exe = proc.exe()
    except Exception:  # noqa: BLE001 - 进程已退出
        return False
    if not self_exe or not exe:
        return False
    try:
        return Path(exe).resolve() == Path(self_exe).resolve()
    except OSError:
        return False


def _is_worker_cmdline(cmdline: Iterable[str]) -> bool:
    text = " ".join(cmdline or ()).lower()
    return "--multiprocessing-fork" in text or "spawn_main" in text


def find_orphan_workers(
    *,
    process_iter: Callable[..., Iterable[Any]] | None = None,
    parent_alive: Callable[[int], bool] | None = None,
    self_exe: str | None = None,
    self_name: str | None = None,
) -> list[dict[str, Any]]:
    """查找父进程已消失的工作进程（父进程崩溃后留下的残留）。"""
    import sys

    if process_iter is None:
        import psutil

        process_iter = psutil.process_iter
    if parent_alive is None:
        import psutil

        def parent_alive(pid: int) -> bool:  # noqa: F811
            try:
                return psutil.Process(pid).is_running()
            except Exception:  # noqa: BLE001 - 进程不存在
                return False

    self_exe = self_exe or sys.executable
    self_name = self_name or Path(sys.executable).name
    found: list[dict[str, Any]] = []
    for proc in process_iter(attrs=["pid", "ppid", "name", "cmdline"]):
        try:
            info = getattr(proc, "info", None) or {}
            pid = int(info.get("pid") or 0)
            ppid = int(info.get("ppid") or 0)
            if not pid or not _is_worker_cmdline(info.get("cmdline")):
                continue
            if not _same_interpreter(proc, self_exe, self_name):
                continue
            if ppid and parent_alive(ppid):
                continue
            found.append({"pid": pid, "ppid": ppid, "name": info.get("name")})
        except Exception:  # noqa: BLE001 - 进程可能刚退出
            continue
    return found


def terminate_orphan_workers(
    *, process_iter: Callable[..., Iterable[Any]] | None = None
) -> list[int]:
    import psutil

    terminated: list[int] = []
    for item in find_orphan_workers(process_iter=process_iter):
        try:
            process = psutil.Process(item["pid"])
            process.terminate()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                process.kill()
            terminated.append(item["pid"])
            logger.warning("已终止残留工作进程：pid=%s", item["pid"])
        except Exception:  # noqa: BLE001 - 可能已退出
            continue
    return terminated


def terminate_registered_workers() -> list[int]:
    """终止上次运行登记下来、仍然存活的残留工作进程。"""
    import psutil

    terminated: list[int] = []
    for pid in worker_registry.list_pids():
        try:
            process = psutil.Process(pid)
            process.terminate()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                process.kill()
            terminated.append(pid)
            logger.warning("已终止上次残留的工作进程：pid=%s", pid)
        except Exception:  # noqa: BLE001 - 进程已不存在
            continue
    worker_registry.clear()
    return terminated


def force_interrupt_active_tasks() -> list[dict[str, Any]]:
    """退出时选择“结束并标记中断”：先标记状态，再由调用方终止工作进程。"""
    return mark_interrupted_tasks()


def recover_on_startup() -> dict[str, Any]:
    """启动时执行：清理残留工作进程并把遗留任务标记为中断。"""
    orphans = terminate_registered_workers()
    orphans += [pid for pid in terminate_orphan_workers() if pid not in orphans]
    interrupted = mark_interrupted_tasks()
    if interrupted:
        logger.info("启动恢复：%s 个任务被标记为 interrupted", len(interrupted))
    return {"orphan_workers": orphans, "interrupted_tasks": interrupted}
