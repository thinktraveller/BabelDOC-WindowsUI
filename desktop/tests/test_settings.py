"""应用数据目录的离线单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from babeldoc_workbench.settings import (
    AppDataDirectoryError,
    build_paths,
    ensure_app_dirs,
)


def test_ensure_app_dirs_creates_layout(tmp_path: Path) -> None:
    paths = ensure_app_dirs(tmp_path / "BabelDOC Workbench")
    for directory in (paths.root, paths.db, paths.tasks, paths.logs, paths.tmp):
        assert directory.is_dir()
    assert paths.log_file.name == "workbench.log"


def test_ensure_app_dirs_reports_unwritable_path(tmp_path: Path) -> None:
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(AppDataDirectoryError) as excinfo:
        ensure_app_dirs(blocker / "sub")
    assert "不可写" in str(excinfo.value)


def test_build_paths_keeps_subdirs_under_root(tmp_path: Path) -> None:
    paths = build_paths(tmp_path)
    assert paths.db.parent == tmp_path
    assert paths.as_dict()["root"] == str(tmp_path)
