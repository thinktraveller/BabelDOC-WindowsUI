"""资源准备：下载、导出离线资源包、导入离线资源包。

引擎在资源下载失败时会直接 ``exit(1)``，因此这里全部在**独立子进程**里执行，
服务进程只读取退出码与脱敏后的输出。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from babeldoc_workbench.logging_setup import redact

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 3600.0
TAIL_CHARS = 4000


class AssetsError(RuntimeError):
    pass


def restore_bundled_assets() -> bool:
    """首次运行完整单文件版时，从内置资源包恢复模型和字体到用户缓存。"""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not bundle_root:
        return False
    archives = list((Path(bundle_root) / "offline_assets").glob("offline_assets_*.zip"))
    if not archives:
        return False
    if len(archives) != 1:
        raise AssetsError("内置离线资源包数量不正确")
    from babeldoc.assets import assets as engine_assets

    try:
        # 引擎按自身清单逐项校验；已存在的正确文件不会重复写入。
        engine_assets.restore_offline_assets_package(archives[0])
    except SystemExit as exc:
        raise AssetsError("内置离线资源包校验或恢复失败") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise AssetsError(f"无法恢复内置资源：{exc}") from exc
    return True


def engine_cache_dir() -> Path:
    from babeldoc_workbench.engine.selfcheck import engine_cache_folder

    return engine_cache_folder()


def cache_usage() -> dict:
    """引擎缓存目录的占用情况（模型、字体、tiktoken、cmap）。"""
    root = engine_cache_dir()
    groups: dict[str, dict[str, int]] = {}
    total = 0
    if root.is_dir():
        for directory in sorted(root.iterdir()):
            if not directory.is_dir():
                continue
            size = 0
            count = 0
            for item in directory.rglob("*"):
                try:
                    if item.is_file():
                        size += item.stat().st_size
                        count += 1
                except OSError:
                    continue
            groups[directory.name] = {"files": count, "bytes": size}
            total += size
    return {"path": str(root), "total_bytes": total, "groups": groups}


def _spawn_self(arguments: list[str], *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """在独立进程里运行本应用的内部命令，避免引擎 exit(1) 影响服务。"""
    if getattr(sys, "frozen", False):
        command = [sys.executable, *arguments]
    else:
        command = [sys.executable, "-m", "babeldoc_workbench.main", *arguments]
    logger.info("启动资源子进程：%s", " ".join(arguments))
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise AssetsError(f"资源操作超时（>{timeout:.0f} 秒）") from exc
    except OSError as exc:
        raise AssetsError(f"无法启动资源子进程：{exc}") from exc
    result = {
        "returncode": completed.returncode,
        "ok": completed.returncode == 0,
        "stdout": redact((completed.stdout or "")[-TAIL_CHARS:]),
        "stderr": redact((completed.stderr or "")[-TAIL_CHARS:]),
    }
    if not result["ok"]:
        logger.warning("资源子进程失败：退出码 %s", completed.returncode)
    return result


def download_assets(*, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """下载并校验缺失的模型、字体与 tiktoken 资源。"""
    return _spawn_self(["--download-assets"], timeout=timeout)


def pack_assets(target_dir: str | Path | None = None) -> dict:
    """生成离线资源包（zip）；默认输出到引擎缓存的 ``assets`` 子目录。"""
    arguments = ["--pack-assets"]
    if target_dir:
        directory = Path(target_dir).expanduser()
        if not directory.is_dir():
            raise AssetsError("目标目录不存在，请重新选择")
        arguments.append(str(directory))
    return _spawn_self(arguments)


def restore_assets(package_path: str | Path, *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """从离线资源包（zip 或目录）还原资源。"""
    path = Path(package_path).expanduser()
    if not path.exists():
        raise AssetsError("找不到离线资源包，请检查路径")
    return _spawn_self(["--restore-assets", str(path)], timeout=timeout)
