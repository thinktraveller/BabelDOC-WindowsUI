"""术语资料库：导入、审核、冲突处理与不可变版本。"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from peewee import IntegrityError

from babeldoc_workbench.models import (
    GLOSSARY_STATUS_APPROVED,
    GLOSSARY_STATUS_CONFLICT,
    GLOSSARY_STATUS_EDITED,
    GLOSSARY_STATUS_NEW,
    GLOSSARY_STATUSES,
    Glossary,
    GlossaryEntry,
    GlossaryVersion,
    Task,
)
from babeldoc_workbench.settings import current_paths

logger = logging.getLogger(__name__)

CSV_COLUMNS = ("source", "target", "tgt_lng")
MAX_ENTRY_LENGTH = 500


class GlossaryError(RuntimeError):
    pass


class GlossaryNotFound(GlossaryError):
    pass


def _normalize_language(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def glossary_dir(glossary_id: int) -> Path:
    directory = current_paths().root / "glossaries" / str(glossary_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def list_glossaries() -> list[dict[str, Any]]:
    items = []
    for glossary in Glossary.select().order_by(Glossary.id.desc()):
        items.append(_public_glossary(glossary))
    return items


def _public_glossary(glossary: Glossary) -> dict[str, Any]:
    entry_count = GlossaryEntry.select().where(GlossaryEntry.glossary == glossary).count()
    conflict_count = (
        GlossaryEntry.select()
        .where(
            (GlossaryEntry.glossary == glossary)
            & (GlossaryEntry.status == GLOSSARY_STATUS_CONFLICT)
        )
        .count()
    )
    version_count = GlossaryVersion.select().where(GlossaryVersion.glossary == glossary).count()
    return {
        "id": glossary.id,
        "name": glossary.name,
        "tgt_lng": glossary.tgt_lng,
        "source_task_id": glossary.source_task_id,
        "entry_count": entry_count,
        "conflict_count": conflict_count,
        "version_count": version_count,
        "created_at": glossary.created_at.isoformat(timespec="seconds")
        if glossary.created_at
        else None,
    }


def get_glossary(glossary_id: int) -> Glossary:
    glossary = Glossary.get_or_none(Glossary.id == glossary_id)
    if glossary is None:
        raise GlossaryNotFound(f"找不到术语表：{glossary_id}")
    return glossary


def create_glossary(
    *, name: str, tgt_lng: str, source_task_id: int | None = None
) -> Glossary:
    name = (name or "").strip()
    if not name:
        raise GlossaryError("术语表名称不能为空")
    language = _normalize_language(tgt_lng)
    if not language:
        raise GlossaryError("必须指定目标语言")
    return Glossary.create(name=name, tgt_lng=language, source_task_id=source_task_id)


def parse_csv(content: bytes) -> list[dict[str, str]]:
    """解析术语 CSV；列名不对或内容为空时抛 :class:`GlossaryError`。"""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [name.strip().lower() for name in (reader.fieldnames or []) if name]
    missing = [column for column in ("source", "target") if column not in fieldnames]
    if missing:
        raise GlossaryError(
            "术语 CSV 至少需要 source 与 target 两列（可选 tgt_lng）"
        )
    rows: list[dict[str, str]] = []
    for row in reader:
        source = str(row.get("source") or "").strip()
        target = str(row.get("target") or "").strip()
        if not source or not target:
            continue
        if len(source) > MAX_ENTRY_LENGTH or len(target) > MAX_ENTRY_LENGTH:
            continue
        rows.append(
            {
                "source": source,
                "target": target,
                "tgt_lng": _normalize_language(row.get("tgt_lng") or ""),
            }
        )
    if not rows:
        raise GlossaryError("术语 CSV 里没有可用条目")
    return rows


def import_rows(
    glossary: Glossary, rows: list[dict[str, str]]
) -> dict[str, int]:
    """导入条目；同键不同译词时标记冲突，绝不静默覆盖。"""
    summary = {"added": 0, "unchanged": 0, "conflicts": 0}
    for row in rows:
        tgt_lng = row.get("tgt_lng") or glossary.tgt_lng
        tgt_lng = _normalize_language(tgt_lng)
        if tgt_lng != _normalize_language(glossary.tgt_lng):
            # 与术语表目标语言不一致的行不进入该表
            continue
        source, target = row["source"], row["target"]
        existing = GlossaryEntry.get_or_none(
            (GlossaryEntry.glossary == glossary)
            & (GlossaryEntry.source == source)
            & (GlossaryEntry.tgt_lng == tgt_lng)
        )
        if existing is None:
            GlossaryEntry.create(
                glossary=glossary,
                source=source,
                target=target,
                tgt_lng=tgt_lng,
                status=GLOSSARY_STATUS_NEW,
            )
            summary["added"] += 1
            continue
        if existing.target == target:
            summary["unchanged"] += 1
            continue
        existing.status = GLOSSARY_STATUS_CONFLICT
        existing.conflict_target = target
        existing.updated_at = datetime.now()
        existing.save()
        summary["conflicts"] += 1
    logger.info(
        "导入术语：新增 %s，未变 %s，冲突 %s",
        summary["added"],
        summary["unchanged"],
        summary["conflicts"],
    )
    return summary


def import_from_file(
    source: BinaryIO | Path, *, name: str, tgt_lng: str
) -> dict[str, Any]:
    content = (
        source.read_bytes() if isinstance(source, Path) else source.read()
    )
    rows = parse_csv(content)
    glossary = create_glossary(name=name, tgt_lng=tgt_lng)
    summary = import_rows(glossary, rows)
    return {**_public_glossary(glossary), "summary": summary}


def import_from_task(task_id: int, *, name: str | None = None) -> dict[str, Any]:
    """把某个任务提取出的 ``*.glossary.csv`` 导入为待审核术语表。"""
    import json

    task = Task.get_or_none(Task.id == task_id)
    if task is None:
        raise GlossaryNotFound(f"找不到任务：{task_id}")
    output_dir = Path(task.output_dir)
    candidates = sorted(output_dir.glob("*.glossary.csv")) if output_dir.is_dir() else []
    if not candidates:
        raise GlossaryError("该任务没有术语提取结果（可能未开启自动提取术语）")
    path = candidates[0]
    try:
        params = json.loads(task.params_snapshot_json or "{}")
    except Exception:  # pragma: no cover - 快照损坏
        params = {}
    tgt_lng = _normalize_language(params.get("lang_out") or "zh")
    glossary_name = (name or f"{task.input_name} · 术语").strip()
    rows = parse_csv(path.read_bytes())
    glossary = create_glossary(
        name=glossary_name, tgt_lng=tgt_lng, source_task_id=task.id
    )
    summary = import_rows(glossary, rows)
    return {**_public_glossary(glossary), "summary": summary}


def list_entries(
    glossary_id: int,
    *,
    tgt_lng: str | None = None,
    status: str | None = None,
    query: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    get_glossary(glossary_id)
    expression = GlossaryEntry.glossary == glossary_id
    if tgt_lng:
        expression &= GlossaryEntry.tgt_lng == _normalize_language(tgt_lng)
    if status:
        if status not in GLOSSARY_STATUSES:
            raise GlossaryError(f"未知状态：{status}")
        expression &= GlossaryEntry.status == status
    if query:
        text = f"%{query.strip()}%"
        expression &= (GlossaryEntry.source ** text) | (GlossaryEntry.target ** text)
    total = GlossaryEntry.select().where(expression).count()
    rows = (
        GlossaryEntry.select()
        .where(expression)
        .order_by(GlossaryEntry.id)
        .limit(limit)
        .offset(offset)
    )
    return {
        "total": total,
        "items": [_public_entry(entry) for entry in rows],
    }


def _public_entry(entry: GlossaryEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "glossary_id": entry.glossary_id,
        "source": entry.source,
        "target": entry.target,
        "tgt_lng": entry.tgt_lng,
        "status": entry.status,
        "conflict_target": entry.conflict_target,
        "updated_at": entry.updated_at.isoformat(timespec="seconds")
        if entry.updated_at
        else None,
    }


def get_entry(entry_id: int) -> GlossaryEntry:
    entry = GlossaryEntry.get_or_none(GlossaryEntry.id == entry_id)
    if entry is None:
        raise GlossaryNotFound(f"找不到术语条目：{entry_id}")
    return entry


def update_entry(
    entry_id: int,
    *,
    source: str | None = None,
    target: str | None = None,
    tgt_lng: str | None = None,
) -> dict[str, Any]:
    entry = get_entry(entry_id)
    new_source = source.strip() if isinstance(source, str) else entry.source
    new_target = target.strip() if isinstance(target, str) else entry.target
    new_lang = _normalize_language(tgt_lng) if tgt_lng else entry.tgt_lng
    if not new_source or not new_target:
        raise GlossaryError("源词与译词都不能为空")
    try:
        entry.source = new_source
        entry.target = new_target
        entry.tgt_lng = new_lang
        entry.status = GLOSSARY_STATUS_EDITED
        entry.conflict_target = None
        entry.updated_at = datetime.now()
        entry.save()
    except IntegrityError as exc:
        raise GlossaryError(
            f"已存在相同源词与目标语言的条目：{new_source}（{new_lang}）"
        ) from exc
    return _public_entry(entry)


def delete_entry(entry_id: int) -> None:
    entry = get_entry(entry_id)
    entry.delete_instance()


def set_status(entry_ids: list[int], status: str) -> int:
    if status not in GLOSSARY_STATUSES:
        raise GlossaryError(f"未知状态：{status}")
    if status == GLOSSARY_STATUS_CONFLICT:
        raise GlossaryError("冲突状态由导入或编辑流程设置，不能手动标记")
    if not entry_ids:
        return 0
    updated = (
        GlossaryEntry.update(status=status, updated_at=datetime.now())
        .where(GlossaryEntry.id << entry_ids)
        .execute()
    )
    return int(updated)


def resolve_conflict(entry_id: int, *, keep: str) -> dict[str, Any]:
    """冲突解决：``existing`` 保留当前译词，``incoming`` 采用导入的新译词。"""
    entry = get_entry(entry_id)
    if entry.status != GLOSSARY_STATUS_CONFLICT:
        raise GlossaryError("该条目当前没有冲突")
    if keep not in {"existing", "incoming"}:
        raise GlossaryError("请选择保留当前译词还是采用新译词")
    if keep == "incoming":
        if not entry.conflict_target:
            raise GlossaryError("冲突候选值缺失，请重新导入")
        entry.target = entry.conflict_target
    entry.conflict_target = None
    entry.status = GLOSSARY_STATUS_EDITED if keep == "incoming" else GLOSSARY_STATUS_APPROVED
    entry.updated_at = datetime.now()
    entry.save()
    return _public_entry(entry)


def _entries_for_snapshot(glossary: Glossary) -> list[GlossaryEntry]:
    return list(
        GlossaryEntry.select()
        .where(GlossaryEntry.glossary == glossary)
        .order_by(GlossaryEntry.id)
    )


def create_version(glossary_id: int, *, note: str | None = None) -> dict[str, Any]:
    """生成不可变快照；存在未解决冲突时拒绝创建。"""
    glossary = get_glossary(glossary_id)
    entries = _entries_for_snapshot(glossary)
    conflicts = [entry for entry in entries if entry.status == GLOSSARY_STATUS_CONFLICT]
    if conflicts:
        raise GlossaryError(
            f"还有 {len(conflicts)} 条冲突未处理，请先解决冲突再生成版本"
        )
    if not entries:
        raise GlossaryError("术语表为空，无法生成版本")
    last = (
        GlossaryVersion.select()
        .where(GlossaryVersion.glossary == glossary)
        .order_by(GlossaryVersion.version.desc())
        .first()
    )
    version_number = (last.version + 1) if last else 1
    snapshot_path = glossary_dir(glossary.id) / f"v{version_number}.csv"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS), doublequote=True)
    writer.writeheader()
    for entry in entries:
        writer.writerow(
            {"source": entry.source, "target": entry.target, "tgt_lng": entry.tgt_lng}
        )
    snapshot_path.write_text(buffer.getvalue(), encoding="utf-8-sig")
    version = GlossaryVersion.create(
        glossary=glossary,
        version=version_number,
        snapshot_path=str(snapshot_path),
        note=(note or None),
        entry_count=len(entries),
    )
    logger.info("生成术语版本：%s v%s（%s 条）", glossary.name, version_number, len(entries))
    return _public_version(version)


def _public_version(version: GlossaryVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "glossary_id": version.glossary_id,
        "version": version.version,
        "note": version.note,
        "entry_count": version.entry_count,
        "snapshot_path": version.snapshot_path,
        "created_at": version.created_at.isoformat(timespec="seconds")
        if version.created_at
        else None,
    }


def list_versions(
    *, glossary_id: int | None = None, tgt_lng: str | None = None
) -> list[dict[str, Any]]:
    query = GlossaryVersion.select().order_by(GlossaryVersion.id.desc())
    if glossary_id is not None:
        query = query.where(GlossaryVersion.glossary == glossary_id)
    items = []
    for version in query:
        data = _public_version(version)
        if tgt_lng:
            glossary = get_glossary(version.glossary_id)
            if _normalize_language(glossary.tgt_lng) != _normalize_language(tgt_lng):
                continue
            data["glossary_name"] = glossary.name
        items.append(data)
    return items


def get_version(version_id: int) -> GlossaryVersion:
    version = GlossaryVersion.get_or_none(GlossaryVersion.id == version_id)
    if version is None:
        raise GlossaryNotFound(f"找不到术语版本：{version_id}")
    return version


def version_for_task(version_id: int) -> tuple[GlossaryVersion, Glossary]:
    version = get_version(version_id)
    glossary = get_glossary(version.glossary_id)
    path = Path(version.snapshot_path)
    if not path.is_file():
        raise GlossaryError("术语版本快照文件已丢失，请重新生成版本")
    return version, glossary


def export_csv(glossary_id: int) -> tuple[str, str]:
    """导出引擎可读 CSV，返回 (文件名, 内容)。"""
    glossary = get_glossary(glossary_id)
    entries = [
        entry
        for entry in _entries_for_snapshot(glossary)
        if entry.status != GLOSSARY_STATUS_CONFLICT
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS), doublequote=True)
    writer.writeheader()
    for entry in entries:
        writer.writerow(
            {"source": entry.source, "target": entry.target, "tgt_lng": entry.tgt_lng}
        )
    return f"{glossary.name}.glossary.csv", buffer.getvalue()


def glossary_versions_of_task(task_id: int) -> int | None:
    """该任务是从哪个术语表导入的（用于任务详情回溯）。"""
    glossary = (
        Glossary.select().where(Glossary.source_task_id == task_id).order_by(Glossary.id).first()
    )
    return glossary.id if glossary else None
