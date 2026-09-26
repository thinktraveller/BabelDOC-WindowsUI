"""全局设置：默认输出目录、成果保留策略与日志级别。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from babeldoc_workbench.models import AppSetting

logger = logging.getLogger(__name__)

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
DEFAULTS: dict[str, Any] = {
    "default_output_dir": "",
    "default_mono_output_dir": None,
    "default_dual_output_dir": None,
    "default_glossary_output_dir": None,
    "retention_days": 0,
    "log_level": "INFO",
}


class SettingsError(RuntimeError):
    pass


def get_settings() -> dict[str, Any]:
    values = dict(DEFAULTS)
    for row in AppSetting.select().where(AppSetting.key << list(DEFAULTS)):
        values[row.key] = row.value
    for key in ("default_mono_output_dir", "default_dual_output_dir", "default_glossary_output_dir"):
        if values[key] is None:
            values[key] = values["default_output_dir"]
    values["retention_days"] = _to_int(values.get("retention_days"), 0)
    return values


def _to_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    unknown = set(patch) - set(DEFAULTS)
    if unknown:
        raise SettingsError(f"未知设置项：{sorted(unknown)}")
    normalized: dict[str, Any] = {}
    for key, label in (
        ("default_output_dir", "默认输出目录"),
        ("default_mono_output_dir", "单语 PDF 输出目录"),
        ("default_dual_output_dir", "双语 PDF 输出目录"),
        ("default_glossary_output_dir", "术语 CSV 输出目录"),
    ):
        if key not in patch:
            continue
        raw = str(patch[key] or "").strip()
        if raw:
            directory = Path(raw).expanduser().resolve()
            if not directory.is_dir():
                raise SettingsError(f"{label}不存在，请重新选择")
            normalized[key] = str(directory)
        else:
            normalized[key] = ""
    if "retention_days" in patch:
        days = _to_int(patch["retention_days"], -1)
        if days < 0:
            raise SettingsError("成果保留天数必须是不小于 0 的整数（0 表示不自动清理）")
        normalized["retention_days"] = str(days)
    if "log_level" in patch:
        level = str(patch["log_level"] or "").upper()
        if level not in LOG_LEVELS:
            raise SettingsError("日志级别必须是 " + " / ".join(LOG_LEVELS))
        normalized["log_level"] = level
        apply_log_level(level)

    for key, value in normalized.items():
        row, created = AppSetting.get_or_create(key=key, defaults={"value": value})
        if not created:
            row.value = value
            row.save()
    return get_settings()


def apply_log_level(level: str) -> None:
    numeric = getattr(logging, level.upper(), None)
    if isinstance(numeric, int):
        logging.getLogger().setLevel(numeric)
        logger.info("日志级别已调整为 %s", level.upper())


def default_output_dir() -> Path | None:
    value = get_settings().get("default_output_dir") or ""
    return Path(value) if value else None


def output_dirs() -> dict[str, Path]:
    """只对尚未写入的新字段继承旧版单目录；显式清空即停止该类导出。"""
    values = get_settings()
    paths = {
        "mono": values["default_mono_output_dir"],
        "dual": values["default_dual_output_dir"],
        "glossary": values["default_glossary_output_dir"],
    }
    return {kind: Path(value) for kind, value in paths.items() if value}
