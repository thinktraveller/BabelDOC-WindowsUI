"""任务持久化：状态、事件、成果索引。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from babeldoc_workbench.db import database
from babeldoc_workbench.engine.progress import stage_label
from babeldoc_workbench.models import (
    TASK_ACTIVE_STATUSES,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
    TASK_STATUSES,
    Task,
    TaskEvent,
    TaskOutput,
)

logger = logging.getLogger(__name__)

EVENT_RETENTION = 200
OUTPUT_PATTERNS = {
    "mono": "*.mono.pdf",
    "dual": "*.dual.pdf",
    "glossary": "*.glossary.csv",
}
EVENT_LOG_NAME = "events.jsonl"


class TaskError(RuntimeError):
    pass


class TaskNotFound(TaskError):
    pass


def create_task(
    *,
    input_name: str,
    stored_input_path: Path,
    output_dir: Path,
    work_dir: Path,
    log_dir: Path,
    api_profile_id: int | None,
    params_snapshot: dict[str, Any],
    glossary_version_id: int | None = None,
    status: str = "preparing",
) -> Task:
    """创建任务行。

    初始状态是 ``preparing``：此时任务目录与输入副本尚未就绪，队列不会取走它。
    路径写好后由 :func:`finalize_new_task` 置为 ``queued`` 并唤醒队列。
    """
    return Task.create(
        status=status,
        input_name=input_name,
        stored_input_path=str(stored_input_path),
        output_dir=str(output_dir),
        work_dir=str(work_dir),
        log_dir=str(log_dir),
        api_profile_id=api_profile_id,
        params_snapshot_json=json.dumps(params_snapshot, ensure_ascii=False),
        glossary_version_id=glossary_version_id,
    )


def finalize_new_task(task: Task, *, input_path: Path, dirs: dict[str, Path]) -> Task:
    """写入最终路径并把任务置为 ``queued``。"""
    task.stored_input_path = str(input_path)
    task.output_dir = str(dirs["output"])
    task.work_dir = str(dirs["work"])
    task.log_dir = str(dirs["logs"])
    task.status = TASK_STATUS_QUEUED
    task.save()
    append_event(task, "status", {"status": TASK_STATUS_QUEUED})
    return task


def get_task(task_id: int) -> Task:
    task = Task.get_or_none(Task.id == task_id)
    if task is None:
        raise TaskNotFound(f"找不到任务：{task_id}")
    return task


def next_queued_task() -> Task | None:
    return (
        Task.select()
        .where(Task.status == TASK_STATUS_QUEUED)
        .order_by(Task.id)
        .first()
    )


def claim_next_queued() -> Task | None:
    """队列消费者取下一个任务并置为 ``preparing``（单消费者，无需抢锁）。"""
    with database.atomic():
        task = next_queued_task()
        if task is None:
            return None
        task.status = "preparing"
        task.started_at = datetime.now()
        task.save()
        return task


def set_status(task: Task, status: str, **fields: Any) -> Task:
    if status not in TASK_STATUSES:
        raise TaskError(f"未知任务状态：{status}")
    task.status = status
    for key, value in fields.items():
        setattr(task, key, value)
    task.save()
    return task


def params_snapshot(task: Task) -> dict[str, Any]:
    try:
        return json.loads(task.params_snapshot_json or "{}")
    except json.JSONDecodeError:
        return {}


def append_event(
    task: Task, event_type: str, payload: dict[str, Any], *, keep: int = EVENT_RETENTION
) -> int:
    """写入事件并滚动保留最近 ``keep`` 条。"""
    last = (
        TaskEvent.select(TaskEvent.sequence)
        .where(TaskEvent.task == task)
        .order_by(TaskEvent.sequence.desc())
        .first()
    )
    sequence = (last.sequence + 1) if last else 1
    TaskEvent.create(
        task=task,
        sequence=sequence,
        type=event_type,
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    if sequence % 25 == 0:
        stale = [
            row.id
            for row in TaskEvent.select(TaskEvent.id)
            .where(TaskEvent.task == task)
            .order_by(TaskEvent.id.desc())
            .offset(keep)
        ]
        if stale:
            TaskEvent.delete().where(TaskEvent.id << stale).execute()
    return sequence


def recent_events(task_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        TaskEvent.select()
        .where(TaskEvent.task == task_id)
        .order_by(TaskEvent.id.desc())
        .limit(limit)
    )
    items = [
        {
            "sequence": row.sequence,
            "type": row.type,
            "payload": json.loads(row.payload_json or "{}"),
            "ts": row.ts.isoformat(timespec="seconds") if row.ts else None,
        }
        for row in rows
    ]
    return list(reversed(items))


def refresh_outputs(task: Task) -> list[dict[str, Any]]:
    """扫描输出目录并重建成果索引。"""
    output_dir = _safe_dir(task.output_dir)
    entries: list[dict[str, Any]] = []
    for kind, pattern in OUTPUT_PATTERNS.items():
        for path in (
            sorted(output_dir.glob(pattern))
            if output_dir is not None and output_dir.is_dir()
            else []
        ):
            entries.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "exists": True,
                }
            )
    log_dir = _safe_dir(task.log_dir)
    event_log = log_dir / EVENT_LOG_NAME if log_dir is not None else None
    if event_log is not None and event_log.is_file():
        entries.append(
            {
                "kind": "log",
                "path": str(event_log),
                "size": event_log.stat().st_size,
                "exists": True,
            }
        )
    with database.atomic():
        TaskOutput.delete().where(TaskOutput.task == task).execute()
        for entry in entries:
            TaskOutput.create(task=task, **entry)
    return entries


def _safe_dir(value: str | None) -> Path | None:
    """拒绝空路径与 ``.``，避免把当前工作目录当成任务目录写入。"""
    text = str(value or "").strip()
    if not text or text == ".":
        return None
    path = Path(text)
    return path if path.is_absolute() else None


def outputs_of(task: Task, *, recheck: bool = True) -> list[dict[str, Any]]:
    """返回成果索引；``recheck`` 时校验文件是否仍然存在。"""
    items: list[dict[str, Any]] = []
    for row in TaskOutput.select().where(TaskOutput.task == task).order_by(TaskOutput.id):
        exists = Path(row.path).is_file() if recheck else row.exists
        items.append(
            {
                "kind": row.kind,
                "path": row.path,
                "size": row.size,
                "exists": bool(exists),
            }
        )
    return items


def public_task(task: Task, *, include_events: bool = False) -> dict[str, Any]:
    stage = task.stage
    payload = {
        "id": task.id,
        "status": task.status,
        "input_name": task.input_name,
        "api_profile_id": task.api_profile_id,
        "glossary_version_id": task.glossary_version_id,
        "engine_version": task.engine_version,
        "stage": stage,
        "stage_label": stage_label(stage) if stage else None,
        "progress": task.progress,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "cancel_requested": bool(task.cancel_requested),
        "created_at": _iso(task.created_at),
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.finished_at),
        "params": params_snapshot(task),
        "outputs": outputs_of(task),
        "output_dir": task.output_dir,
        "log_dir": task.log_dir,
    }
    if include_events:
        payload["events"] = recent_events(task.id)
    return payload


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def list_tasks(*, limit: int = 100, status: str | None = None) -> list[Task]:
    query = Task.select().order_by(Task.id.desc()).limit(limit)
    if status:
        query = query.where(Task.status == status)
    return list(query)


def active_tasks() -> list[Task]:
    return list(Task.select().where(Task.status << list(TASK_ACTIVE_STATUSES)))


def mark_failed(task: Task, *, code: str, message: str) -> Task:
    return set_status(
        task,
        TASK_STATUS_FAILED,
        error_code=code,
        error_message=message,
        finished_at=datetime.now(),
    )


def mark_cancelled(task: Task) -> Task:
    return set_status(
        task,
        TASK_STATUS_CANCELLED,
        finished_at=datetime.now(),
        cancel_requested=True,
    )


def mark_succeeded(task: Task) -> Task:
    return set_status(
        task,
        TASK_STATUS_SUCCEEDED,
        error_code=None,
        error_message=None,
        finished_at=datetime.now(),
        progress=100.0,
    )


def mark_running(task: Task) -> Task:
    return set_status(task, TASK_STATUS_RUNNING, started_at=task.started_at or datetime.now())
