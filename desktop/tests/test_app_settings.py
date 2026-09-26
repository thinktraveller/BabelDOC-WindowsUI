"""全局设置的离线单元测试。"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from babeldoc_workbench.services import app_settings


def test_defaults_when_empty(workbench_db) -> None:
    values = app_settings.get_settings()
    assert values["default_output_dir"] == ""
    assert values["default_mono_output_dir"] == ""
    assert values["default_dual_output_dir"] == ""
    assert values["default_glossary_output_dir"] == ""
    assert app_settings.output_dirs() == {}
    assert values["retention_days"] == 0
    assert values["log_level"] == "INFO"


def test_update_default_output_dir(workbench_db, tmp_path: Path) -> None:
    target = tmp_path / "译文"
    target.mkdir()
    values = app_settings.update_settings({"default_output_dir": str(target)})
    assert values["default_output_dir"] == str(target)
    assert app_settings.default_output_dir() == target
    assert app_settings.output_dirs() == {
        "mono": target, "dual": target, "glossary": target,
    }
    app_settings.update_settings({"default_dual_output_dir": ""})
    assert app_settings.output_dirs() == {"mono": target, "glossary": target}


def test_independent_output_directories(workbench_db, tmp_path: Path) -> None:
    paths = {kind: tmp_path / kind for kind in ("mono", "dual", "glossary")}
    for path in paths.values():
        path.mkdir()
    values = app_settings.update_settings({
        f"default_{kind}_output_dir": str(path) for kind, path in paths.items()
    })
    assert app_settings.output_dirs() == paths
    assert all(values[f"default_{kind}_output_dir"] == str(path) for kind, path in paths.items())


def test_update_rejects_missing_directory(workbench_db, tmp_path: Path) -> None:
    with pytest.raises(app_settings.SettingsError):
        app_settings.update_settings({"default_output_dir": str(tmp_path / "缺失")})
    with pytest.raises(app_settings.SettingsError):
        app_settings.update_settings({"default_mono_output_dir": str(tmp_path / "缺失")})


def test_update_rejects_unknown_key(workbench_db) -> None:
    with pytest.raises(app_settings.SettingsError):
        app_settings.update_settings({"unknown": 1})


def test_retention_must_be_non_negative(workbench_db) -> None:
    with pytest.raises(app_settings.SettingsError):
        app_settings.update_settings({"retention_days": -3})
    assert app_settings.update_settings({"retention_days": 30})["retention_days"] == 30


def test_log_level_is_applied(workbench_db) -> None:
    original = logging.getLogger().level
    try:
        values = app_settings.update_settings({"log_level": "DEBUG"})
        assert values["log_level"] == "DEBUG"
        assert logging.getLogger().level == logging.DEBUG
        with pytest.raises(app_settings.SettingsError):
            app_settings.update_settings({"log_level": "TRACE"})
    finally:
        logging.getLogger().setLevel(original)
