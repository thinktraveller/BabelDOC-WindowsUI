"""参数预设的离线单元测试。"""

from __future__ import annotations

import pytest

from babeldoc_workbench.services import presets


def test_save_list_load_delete_roundtrip(workbench_db) -> None:
    saved = presets.save_preset("论文默认", {"lang_in": "en", "lang_out": "zh", "qps": 6})
    assert saved["params"]["qps"] == 6

    listed = presets.list_presets()
    assert [item["name"] for item in listed] == ["论文默认"]

    loaded = presets.load_preset(saved["id"])
    assert loaded["params"]["lang_in"] == "en"

    presets.delete_preset(saved["id"])
    assert presets.list_presets() == []


def test_save_same_name_overwrites(workbench_db) -> None:
    first = presets.save_preset("常用", {"qps": 2})
    second = presets.save_preset("常用", {"qps": 9})
    assert first["id"] == second["id"]
    assert presets.load_preset(first["id"])["params"]["qps"] == 9


def test_invalid_params_are_rejected(workbench_db) -> None:
    with pytest.raises(presets.PresetError):
        presets.save_preset("坏预设", {"qps": 0})
    with pytest.raises(presets.PresetError):
        presets.save_preset("", {"qps": 4})
    with pytest.raises(presets.PresetError):
        presets.save_preset("未知参数", {"table_model": "x"})


def test_key_like_fields_never_get_saved(workbench_db) -> None:
    saved = presets.save_preset(
        "带凭据字段", {"qps": 3, "api_key": "sk-fake-should-be-dropped"}
    )
    dumped = str(presets.load_preset(saved["id"]))
    assert "sk-fake-should-be-dropped" not in dumped
    assert "api_key" not in dumped


def test_missing_preset_raises(workbench_db) -> None:
    with pytest.raises(presets.PresetNotFound):
        presets.load_preset(12345)
