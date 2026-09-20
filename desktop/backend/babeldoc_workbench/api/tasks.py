"""任务接口：提交、查询、事件流（SSE）、取消与重新执行。"""

from __future__ import annotations

import asyncio
import logging
import queue as queue_module
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from babeldoc_workbench.engine.protocol import TERMINAL_EVENT_TYPES
from babeldoc_workbench.models import TASK_TERMINAL_STATUSES, TASK_STATUSES
from babeldoc_workbench.pdfinfo import PdfInfoError, page_count
from babeldoc_workbench.services import files as file_store
from babeldoc_workbench.services import params as params_service
from babeldoc_workbench.services import task_store
from babeldoc_workbench.services.task_queue import (
    SKIP_TRANSLATION_KEY,
    TaskQueue,
    allow_skip_translation,
    ensure_task_dirs,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_queue: TaskQueue | None = None
HEARTBEAT_SECONDS = 15.0


def set_queue(queue: TaskQueue | None) -> None:
    global _queue
    _queue = queue


def get_queue() -> TaskQueue:
    if _queue is None:
        raise HTTPException(status_code=503, detail="任务队列尚未启动")
    return _queue


class TaskCreatePayload(BaseModel):
    file_ids: list[str]
    api_profile_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    glossary_tgt_lng: str | None = None
    # 仅自动验证使用：只有服务端设置了 BABELDOC_ALLOW_SKIP_TRANSLATION=1 才生效
    skip_translation: bool = False


def _validate_or_400(
    payload: TaskCreatePayload, *, pages: int | None
) -> dict[str, Any]:
    glossary = (
        {"tgt_lng": payload.glossary_tgt_lng} if payload.glossary_tgt_lng else None
    )
    result = params_service.validate_params(
        payload.params, page_count=pages, glossary=glossary
    )
    if not result.ok:
        raise HTTPException(
            status_code=400, detail={"message": "参数不合法", "errors": result.errors}
        )
    snapshot = dict(result.params)
    if payload.skip_translation:
        if not allow_skip_translation():
            raise HTTPException(
                status_code=400, detail="skip_translation 仅在开发验证模式下可用"
            )
        snapshot[SKIP_TRANSLATION_KEY] = True
    return snapshot


@router.post("", status_code=201)
def create_tasks(payload: TaskCreatePayload) -> dict:
    if not payload.file_ids:
        raise HTTPException(status_code=400, detail="至少选择一个文件")
    if payload.api_profile_id is None and not payload.skip_translation:
        raise HTTPException(status_code=400, detail="请先选择 API 配置")

    prepared: list[dict[str, Any]] = []
    for file_id in payload.file_ids:
        try:
            staged = file_store.describe(file_id)
        except file_store.FileStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            pages = page_count(staged["path"])
        except PdfInfoError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        prepared.append({"staged": staged, "snapshot": _validate_or_400(payload, pages=pages)})

    queue = get_queue()
    created = []
    for item in prepared:
        staged = item["staged"]
        snapshot = item["snapshot"]
        task = task_store.create_task(
            input_name=staged["name"],
            stored_input_path=Path(staged["path"]),
            output_dir=Path("."),
            work_dir=Path("."),
            log_dir=Path("."),
            api_profile_id=payload.api_profile_id,
            params_snapshot=snapshot,
            glossary_version_id=snapshot.get("glossary_version_id"),
        )
        dirs = ensure_task_dirs(task.id)
        input_target = dirs["input"] / staged["name"]
        try:
            shutil.copyfile(staged["path"], input_target)
        except OSError as exc:
            task.delete_instance(recursive=True)
            raise HTTPException(
                status_code=500, detail=f"复制文件到任务目录失败：{exc}"
            ) from exc
        task_store.finalize_new_task(task, input_path=input_target, dirs=dirs)
        queue.submit(task.id)
        created.append(task_store.public_task(task))
    return {"items": created}


@router.get("")
def list_tasks(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    if status and status not in TASK_STATUSES:
        raise HTTPException(status_code=400, detail=f"未知状态：{status}")
    tasks = task_store.list_tasks(limit=limit, status=status)
    return {"items": [task_store.public_task(task) for task in tasks]}


@router.get("/{task_id}")
def get_task(task_id: int) -> dict:
    try:
        task = task_store.get_task(task_id)
    except task_store.TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return task_store.public_task(task, include_events=True)


@router.get("/{task_id}/events")
async def stream_events(task_id: int, request: Request) -> StreamingResponse:
    try:
        task_store.get_task(task_id)
    except task_store.TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    queue = get_queue()
    subscriber = queue.subscribe(task_id)

    async def event_stream():
        try:
            snapshot_task = task_store.get_task(task_id)
            snapshot = task_store.public_task(snapshot_task, include_events=True)
            yield _sse("snapshot", snapshot)
            if snapshot_task.status in TASK_TERMINAL_STATUSES:
                # 已经是结束态：发一次状态事件后立即收尾，避免客户端一直等待
                yield _sse(
                    "status",
                    {"status": snapshot_task.status, "from_snapshot": True},
                )
                return
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = subscriber.get_nowait()
                except queue_module.Empty:
                    await asyncio.sleep(0.2)
                    idle += 0.2
                    if idle >= HEARTBEAT_SECONDS:
                        idle = 0.0
                        yield ": ping\n\n"
                    continue
                idle = 0.0
                event_type = str(item.get("type") or "")
                yield _sse(event_type, item.get("payload") or {})
                if event_type in TERMINAL_EVENT_TYPES:
                    break
        finally:
            queue.unsubscribe(task_id, subscriber)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/{task_id}/cancel")
def cancel_task(task_id: int) -> dict:
    try:
        result = get_queue().cancel(task_id)
    except task_store.TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.post("/{task_id}/rerun", status_code=201)
def rerun_task(task_id: int) -> dict:
    try:
        original = task_store.get_task(task_id)
    except task_store.TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    source = Path(original.stored_input_path)
    if not source.is_file():
        raise HTTPException(
            status_code=400, detail="原任务的输入文件已不存在，请重新导入文件后再执行"
        )
    queue = get_queue()
    task = task_store.create_task(
        input_name=original.input_name,
        stored_input_path=source,
        output_dir=Path("."),
        work_dir=Path("."),
        log_dir=Path("."),
        api_profile_id=original.api_profile_id,
        params_snapshot=task_store.params_snapshot(original),
        glossary_version_id=original.glossary_version_id,
    )
    dirs = ensure_task_dirs(task.id)
    target = dirs["input"] / original.input_name
    try:
        shutil.copyfile(source, target)
    except OSError as exc:
        task.delete_instance(recursive=True)
        raise HTTPException(status_code=500, detail=f"复制输入文件失败：{exc}") from exc
    task_store.finalize_new_task(task, input_path=target, dirs=dirs)
    task_store.append_event(task, "status", {"rerun_of": original.id})
    queue.submit(task.id)
    logger.info("重新执行任务 %s → 新任务 %s", original.id, task.id)
    return task_store.public_task(task)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int) -> None:
    """只删除任务记录与索引；文件生命周期操作在步骤 7 单独实现。"""
    try:
        task = task_store.get_task(task_id)
    except task_store.TaskNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if task.status not in TASK_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="任务仍在进行中，请先取消")
    task.delete_instance(recursive=True)


def _sse(event: str, payload: dict[str, Any]) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
