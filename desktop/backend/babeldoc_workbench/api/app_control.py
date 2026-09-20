"""应用级控制接口：运行状态、强制中断与诊断包导出。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from babeldoc_workbench.services import diagnostics, recovery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/app", tags=["app"])


class DiagnosticsPayload(BaseModel):
    destination_dir: str | None = None


@router.get("/status")
def app_status() -> dict:
    active = recovery.active_tasks()
    return {
        "active_count": len(active),
        "active_tasks": [
            {"id": task.id, "status": task.status, "stage": task.stage}
            for task in active
        ],
    }


@router.post("/force-interrupt")
def force_interrupt() -> dict:
    """结束并标记中断：把运行中的任务标记为 interrupted。"""
    interrupted = recovery.force_interrupt_active_tasks()
    return {"interrupted": interrupted}


@router.post("/diagnostics")
def export_diagnostics(payload: DiagnosticsPayload) -> dict:
    destination = None
    if payload.destination_dir:
        candidate = Path(payload.destination_dir).expanduser()
        if not candidate.is_dir():
            raise HTTPException(status_code=400, detail="导出目录不存在，请重新选择")
        destination = candidate
    result = diagnostics.export_diagnostics(destination)
    result["findings"] = diagnostics.scan_package(Path(result["path"]))
    if result["findings"]:
        logger.warning("诊断包自检发现可疑内容：%s", result["findings"])
    return result
