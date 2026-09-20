"""参数预设：保存、加载、删除命名预设（只保存参数，不含任何 Key）。"""

from __future__ import annotations

import json
import logging
from typing import Any

from peewee import IntegrityError

from babeldoc_workbench.models import ParamPreset
from babeldoc_workbench.services.params import PARAM_KEYS, validate_params

logger = logging.getLogger(__name__)

FORBIDDEN_PRESET_KEYS = ("api_key", "apikey", "key", "credential_ref", "token")


class PresetError(RuntimeError):
    pass


class PresetNotFound(PresetError):
    pass


def _sanitize(params: dict[str, Any]) -> dict[str, Any]:
    """剔除任何疑似凭据字段，避免预设里混入 Key。"""
    cleaned = {
        key: value
        for key, value in params.items()
        if key.lower() not in FORBIDDEN_PRESET_KEYS
    }
    unknown = set(cleaned) - PARAM_KEYS
    if unknown:
        raise PresetError(f"预设包含未知参数：{sorted(unknown)}")
    return cleaned


def save_preset(name: str, params: dict[str, Any]) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise PresetError("预设名称不能为空")
    cleaned = _sanitize(dict(params))
    result = validate_params(cleaned)
    if not result.ok:
        raise PresetError("预设参数不合法：" + json.dumps(result.errors, ensure_ascii=False))
    payload = json.dumps(result.params, ensure_ascii=False)
    try:
        preset, created = ParamPreset.get_or_create(
            name=name, defaults={"params_json": payload}
        )
        if not created:
            preset.params_json = payload
            preset.save()
    except IntegrityError as exc:  # pragma: no cover - 并发写入
        raise PresetError(f"预设名称已存在：{name}") from exc
    logger.info("保存参数预设：%s", name)
    return _public(preset)


def _public(preset: ParamPreset) -> dict[str, Any]:
    return {
        "id": preset.id,
        "name": preset.name,
        "params": json.loads(preset.params_json or "{}"),
        "created_at": preset.created_at.isoformat() if preset.created_at else None,
    }


def list_presets() -> list[dict[str, Any]]:
    return [_public(preset) for preset in ParamPreset.select().order_by(ParamPreset.name)]


def load_preset(preset_id: int) -> dict[str, Any]:
    preset = ParamPreset.get_or_none(ParamPreset.id == preset_id)
    if preset is None:
        raise PresetNotFound(f"找不到预设：{preset_id}")
    return _public(preset)


def delete_preset(preset_id: int) -> None:
    preset = ParamPreset.get_or_none(ParamPreset.id == preset_id)
    if preset is None:
        raise PresetNotFound(f"找不到预设：{preset_id}")
    preset.delete_instance()
    logger.info("删除参数预设：%s", preset.name)
