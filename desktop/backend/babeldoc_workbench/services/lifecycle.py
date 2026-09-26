"""成果操作与文件生命周期。

删除语义分三层，互不混淆（计划书步骤 7）：

1. 删除任务记录：只删数据库记录与索引，文件保持不动；
2. 清理任务临时文件：只删 ``work``（与遗留的 ``tmp``），保留输入副本与成果；
3. 删除应用管理的文件：删任务目录内的输入副本、工作目录与成果，永不触碰用户原始导入位置。

所有删除前都把路径解析为绝对路径并校验位于任务根目录之下。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Mapping

from babeldoc_workbench.models import Task
from babeldoc_workbench.services import task_store
from babeldoc_workbench.settings import current_paths

logger = logging.getLogger(__name__)

RESULT_KINDS = frozenset({"mono", "dual", "glossary"})
TEMP_SUBDIRS = ("work", "tmp")
MANAGED_SUBDIRS = ("input", "work", "output", "logs")


class LifecycleError(RuntimeError):
    pass


def tasks_root() -> Path:
    return current_paths().tasks.resolve()


def task_root(task_id: int) -> Path:
    """任务根目录；任何越界路径都在这里被拒绝。"""
    base = tasks_root()
    root = (base / str(task_id)).resolve()
    if root.parent != base:
        raise LifecycleError("拒绝访问任务目录之外的路径")
    return root


def _ensure_inside(root: Path, target: Path) -> Path:
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise LifecycleError("拒绝操作任务目录之外的路径")
    return resolved


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:  # pragma: no cover - 文件被占用或权限不足
            continue
    return total


def describe_task_files(task_id: int) -> dict:
    """列出任务目录内各子目录的路径与体积，用于删除前的确认文案。"""
    root = task_root(task_id)
    items = []
    for name in MANAGED_SUBDIRS:
        directory = root / name
        items.append(
            {
                "name": name,
                "path": str(directory),
                "exists": directory.exists(),
                "size": directory_size(directory),
            }
        )
    return {
        "task_id": task_id,
        "root": str(root),
        "items": items,
        "total_size": sum(item["size"] for item in items),
    }


def clear_temp_files(task_id: int) -> dict:
    """只清理临时文件（``work`` 与遗留 ``tmp``），保留输入副本与成果。"""
    root = task_root(task_id)
    freed = 0
    removed: list[str] = []
    for name in TEMP_SUBDIRS:
        directory = _ensure_inside(root, root / name)
        if not directory.exists():
            continue
        freed += directory_size(directory)
        shutil.rmtree(directory, ignore_errors=True)
        removed.append(str(directory))
    logger.info("清理任务 %s 的临时文件：释放 %s 字节", task_id, freed)
    return {"task_id": task_id, "removed": removed, "freed_bytes": freed}


def delete_managed_files(task_id: int) -> dict:
    """删除任务目录内的输入副本、工作目录与成果（保留任务记录）。"""
    root = task_root(task_id)
    freed = 0
    removed: list[str] = []
    for name in MANAGED_SUBDIRS:
        directory = _ensure_inside(root, root / name)
        if not directory.exists():
            continue
        freed += directory_size(directory)
        shutil.rmtree(directory, ignore_errors=True)
        removed.append(str(directory))
    try:
        task = task_store.get_task(task_id)
    except task_store.TaskNotFound:
        task = None
    if task is not None:
        task_store.refresh_outputs(task)
    logger.info("删除任务 %s 的应用管理文件：释放 %s 字节", task_id, freed)
    return {"task_id": task_id, "removed": removed, "freed_bytes": freed}


def output_path(task: Task, kind: str, *, require_exists: bool = True) -> Path:
    if kind not in RESULT_KINDS:
        raise LifecycleError("只支持打开或另存单语、双语与术语文件")
    for item in task_store.outputs_of(task):
        if item["kind"] == kind:
            path = Path(item["path"])
            if require_exists and not path.is_file():
                raise LifecycleError("文件不存在或被移动，可重新执行该任务")
            return path
    raise LifecycleError("该任务没有此类成果文件")


def reveal_output_path(task: Task, kind: str) -> Path:
    """优先定位已导出的用户副本；没有副本时定位应用管理原件。"""
    output = next(
        (item for item in task_store.outputs_of(task) if item["kind"] == kind), None
    )
    if output is None:
        raise LifecycleError("该任务没有此类成果文件")
    source = Path(output["path"])
    if kind in RESULT_KINDS:
        for event in reversed(task_store.recent_events(task.id)):
            if event["type"] != "exported":
                continue
            for item in event["payload"].get("items", []):
                if item.get("kind") == kind and item.get("source") == str(source):
                    exported = Path(item["path"])
                    if exported.is_file():
                        return exported
            break
    if source.is_file():
        return source
    raise LifecycleError("文件不存在或被移动，可重新执行该任务")


def _shell_open(path: Path, *, select: bool = False) -> None:
    if not sys.platform.startswith("win"):
        raise LifecycleError("当前平台暂不支持直接打开文件")
    if select and path.is_file():
        os.startfile(str(path.parent))  # noqa: S606 - 仅打开本机目录
        return
    os.startfile(str(path))  # noqa: S606


def reveal_in_explorer(path: Path) -> None:
    """在资源管理器中定位文件或目录。"""
    target = Path(path)
    if not target.exists():
        raise LifecycleError("路径不存在，可能已被移动或删除")
    _shell_open(target)


def open_with_default_app(path: Path) -> None:
    target = Path(path)
    if not target.is_file():
        raise LifecycleError("文件不存在或被移动，可重新执行该任务")
    _shell_open(target)


def save_copy(task: Task, kind: str, target_dir: str | Path) -> dict:
    """把成果复制到用户选择的目录（不删除原文件）。"""
    source = output_path(task, kind)
    directory = Path(target_dir).expanduser()
    if not directory.is_dir():
        raise LifecycleError("目标目录不存在，请重新选择")
    destination = directory / source.name
    if destination.exists():
        raise LifecycleError(f"目标目录已存在同名文件：{destination.name}")
    try:
        shutil.copyfile(source, destination)
    except OSError as exc:
        raise LifecycleError(
            f"另存失败：{exc}。如果 PDF 正在阅读器中打开，请先关闭后重试。"
        ) from exc
    logger.info("另存成果：%s → %s", source, destination)
    return {
        "kind": kind,
        "source": str(source),
        "target": str(destination),
        "size": destination.stat().st_size,
    }


def export_copies(task: Task, outputs: list[dict], target_dirs: Mapping[str, Path]) -> dict:
    """成功任务的用户副本；同名文件不覆盖，单项复制失败不改变任务成功状态。"""
    items: list[dict] = []
    errors: list[str] = []
    for output in outputs:
        kind = output["kind"]
        if kind not in RESULT_KINDS or not output["exists"] or kind not in target_dirs:
            continue
        directory = target_dirs[kind].expanduser()
        source = Path(output["path"])
        if not directory.is_dir():
            errors.append(f"{source.name}：默认输出目录不存在：{directory}")
            continue
        for number in range(1000):
            suffix = "" if number == 0 else f"-任务{task.id}" + (f"-{number}" if number > 1 else "")
            target = directory / f"{source.stem}{suffix}{source.suffix}"
            created = False
            try:
                with target.open("xb") as destination:
                    created = True
                    with source.open("rb") as origin:
                        shutil.copyfileobj(origin, destination)
            except FileExistsError:
                continue
            except OSError as exc:
                if created:
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("无法清理未写完的导出文件：%s", target)
                errors.append(f"{source.name}：{exc}")
                break
            items.append({"kind": kind, "source": str(source), "path": str(target)})
            break
        else:
            errors.append(f"{source.name}：同名文件过多，无法生成新文件名")
    return {"items": items, "errors": errors}
