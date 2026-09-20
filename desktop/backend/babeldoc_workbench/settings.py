"""应用数据目录与运行期设置。

约定（计划书「本地数据与凭据」）：应用数据、任务目录、日志与临时文件分开管理，
一律写在 ``%LOCALAPPDATA%\\BabelDOC Workbench``，不写入程序安装目录。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "BabelDOC Workbench"
SUBDIRS = ("db", "tasks", "logs", "tmp")

_current_paths: AppPaths | None = None


class AppDataDirectoryError(RuntimeError):
    """应用数据目录不可用：按约定明确报错，不静默改用其他目录。"""


@dataclass(frozen=True)
class AppPaths:
    """应用数据目录布局。"""

    root: Path
    db: Path
    tasks: Path
    logs: Path
    tmp: Path

    @property
    def log_file(self) -> Path:
        return self.logs / "workbench.log"

    def as_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "db": str(self.db),
            "tasks": str(self.tasks),
            "logs": str(self.logs),
            "tmp": str(self.tmp),
        }


def default_app_data_dir() -> Path:
    """默认应用数据目录，可通过 ``BABELDOC_APP_DIR`` 覆盖（开发与验证用）。"""
    override = os.environ.get("BABELDOC_APP_DIR")
    if override and override.strip():
        return Path(override.strip())
    base = os.environ.get("LOCALAPPDATA")
    if base and base.strip():
        return Path(base.strip()) / APP_DIR_NAME
    return Path.home() / "AppData" / "Local" / APP_DIR_NAME


def build_paths(root: Path) -> AppPaths:
    root = Path(root)
    return AppPaths(
        root=root,
        db=root / "db",
        tasks=root / "tasks",
        logs=root / "logs",
        tmp=root / "tmp",
    )


def ensure_app_dirs(root: Path | None = None) -> AppPaths:
    """创建并验证应用数据目录；不可写时抛 :class:`AppDataDirectoryError`。"""
    paths = build_paths(Path(root) if root is not None else default_app_data_dir())
    try:
        for directory in (paths.root, paths.db, paths.tasks, paths.logs, paths.tmp):
            directory.mkdir(parents=True, exist_ok=True)
        probe = paths.tmp / ".write-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise AppDataDirectoryError(
            f"应用数据目录不可写：{paths.root}（{exc}）。"
            "请检查路径权限或磁盘空间后重试。"
        ) from exc
    return paths


def set_current_paths(paths: AppPaths | None) -> None:
    """记录本次运行使用的应用目录；服务层通过 :func:`current_paths` 读取。"""
    global _current_paths
    _current_paths = paths


def current_paths() -> AppPaths:
    if _current_paths is None:
        raise RuntimeError("应用数据目录尚未初始化")
    return _current_paths
