"""诊断包导出：数据库摘要、参数快照、资源状态与脱敏日志。

导出前统一走 :func:`babeldoc_workbench.logging_setup.redact`，导出后还会再扫描一遍，
确认包内不含 Key 之类敏感串。
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from babeldoc_workbench import __version__ as APP_VERSION
from babeldoc_workbench.db import schema_version
from babeldoc_workbench.engine.selfcheck import run_light_checks
from babeldoc_workbench.logging_setup import redact
from babeldoc_workbench.models import Task
from babeldoc_workbench.services import app_settings, glossaries, task_store
from babeldoc_workbench.settings import current_paths

logger = logging.getLogger(__name__)

PACKAGE_PREFIX = "babeldoc-diagnostics-"
LOG_TAIL_BYTES = 200_000
SUSPICIOUS_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[=:]\s*\S+"),
)


def engine_version() -> str:
    try:
        from babeldoc.const import __version__ as version

        return str(version)
    except Exception:  # noqa: BLE001
        return "unknown"


def app_data_dir() -> Path:
    return current_paths().root


def collect_summary() -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for task in Task.select():
        statuses[task.status] = statuses.get(task.status, 0) + 1
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": APP_VERSION,
        "engine_version": engine_version(),
        "schema_version": schema_version(),
        "app_data_dir": str(app_data_dir()),
        "settings": app_settings.get_settings(),
        "task_status_counts": statuses,
        "glossary_count": len(glossaries.list_glossaries()),
    }


def collect_tasks(limit: int = 200) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for task in Task.select().order_by(Task.id.desc()).limit(limit):
        payload = task_store.public_task(task)
        # 只保留路径与元信息，不包含任何文档正文
        payload.pop("events", None)
        items.append(payload)
    return items


def collect_resources() -> list[dict[str, Any]]:
    return [item.to_dict() for item in run_light_checks()]


def _log_text() -> str:
    log_file = current_paths().log_file
    if not log_file.is_file():
        return ""
    data = log_file.read_bytes()
    if len(data) > LOG_TAIL_BYTES:
        data = data[-LOG_TAIL_BYTES:]
    return redact(data.decode("utf-8", errors="replace"))


def export_diagnostics(destination: Path | None = None) -> dict[str, Any]:
    """生成诊断包（zip），返回路径、体积与条目名。"""
    directory = Path(destination) if destination else Path(tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{PACKAGE_PREFIX}{stamp}.zip"
    files = {
        "summary.json": json.dumps(collect_summary(), ensure_ascii=False, indent=2),
        "tasks.json": json.dumps(collect_tasks(), ensure_ascii=False, indent=2),
        "resources.json": json.dumps(
            collect_resources(), ensure_ascii=False, indent=2
        ),
        "glossaries.json": json.dumps(
            glossaries.list_glossaries(), ensure_ascii=False, indent=2
        ),
        "logs/workbench.log": _log_text(),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, redact(content))
    logger.info("诊断包已导出：%s（%s 字节）", path, path.stat().st_size)
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "entries": sorted(files),
    }


def scan_package(path: Path, needles: Iterable[str] = ()) -> list[str]:
    """扫描诊断包，返回可疑内容描述；空列表表示通过。"""
    findings: list[str] = []
    extra = [needle for needle in needles if needle]
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            content = archive.read(name).decode("utf-8", errors="replace")
            for pattern in SUSPICIOUS_PATTERNS:
                match = pattern.search(content)
                if match:
                    findings.append(f"{name}：命中敏感模式 {match.group(0)[:12]}…")
            for needle in extra:
                if needle in content:
                    findings.append(f"{name}：包含不应出现的内容")
    return findings
