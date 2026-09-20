"""受控文件标识与暂存区。

界面与接口之间不传递任意路径：导入时把文件复制到暂存区并分配标识，后续一律用
标识访问。提交任务时再由队列把文件复制进任务目录的 ``input`` 子目录。
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from babeldoc_workbench.settings import current_paths

logger = logging.getLogger(__name__)

STAGING_DIRNAME = "_staging"
ALLOWED_SUFFIXES = frozenset({".pdf"})
FILE_ID_LENGTH = 32


class FileStoreError(RuntimeError):
    pass


class FileNotFound(FileStoreError):
    pass


def staging_root() -> Path:
    root = current_paths().tasks / STAGING_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_id(file_id: str) -> str:
    """标识必须是 32 位十六进制，避免用路径穿越访问任意文件。"""
    text = str(file_id or "").strip().lower()
    if len(text) != FILE_ID_LENGTH or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise FileNotFound("文件标识无效")
    return text


def _safe_name(filename: str) -> str:
    name = Path(str(filename or "")).name.strip()
    if not name:
        raise FileStoreError("文件名不能为空")
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise FileStoreError("仅支持 PDF 文件")
    return name


def stash(source: BinaryIO | Path, filename: str) -> dict:
    """把上传流或本地文件复制到暂存区，返回受控文件描述。"""
    name = _safe_name(filename)
    file_id = uuid.uuid4().hex
    directory = staging_root() / file_id
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / name
    try:
        if isinstance(source, Path):
            shutil.copyfile(source, target)
        else:
            with target.open("wb") as handle:
                shutil.copyfileobj(source, handle, length=1024 * 1024)
    except OSError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise FileStoreError(f"保存文件失败：{exc}") from exc
    logger.info("已暂存文件：%s（%s 字节）", name, target.stat().st_size)
    return describe(file_id)


def describe(file_id: str) -> dict:
    directory = staging_root() / _validate_id(file_id)
    if not directory.is_dir():
        raise FileNotFound(f"找不到文件：{file_id}")
    files = [item for item in directory.iterdir() if item.is_file()]
    if not files:
        raise FileNotFound(f"暂存区文件已被删除：{file_id}")
    target = files[0]
    return {
        "id": directory.name,
        "name": target.name,
        "size": target.stat().st_size,
        "staged_at": datetime.fromtimestamp(target.stat().st_mtime).isoformat(
            timespec="seconds"
        ),
        "path": str(target),
    }


def resolve(file_id: str) -> Path:
    return Path(describe(file_id)["path"])


def list_files() -> list[dict]:
    root = staging_root()
    items: list[dict] = []
    for directory in sorted(root.iterdir() if root.exists() else []):
        if not directory.is_dir():
            continue
        try:
            items.append(describe(directory.name))
        except FileStoreError:
            continue
    return items


def delete(file_id: str) -> None:
    directory = staging_root() / _validate_id(file_id)
    if not directory.is_dir():
        raise FileNotFound(f"找不到文件：{file_id}")
    shutil.rmtree(directory, ignore_errors=False)
    logger.info("已删除暂存文件：%s", file_id)
