"""运行环境自检：打包前后都使用同一套检查项。

检查项对应计划书步骤 2 的第 4 条：Python 运行时版本、ONNX Runtime provider、
版面模型与字体资源、工作进程能否启动、WebView2 是否可用、应用数据目录是否可写。

本模块只读取环境与资源文件；写操作仅限于“可写性探测”时创建并立即删除的临时文件。
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import struct
import sys
import sysconfig
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

MIN_PYTHON = (3, 12)
MAX_PYTHON_EXCLUSIVE = (3, 14)

ASSET_SUBDIRS = {
    "models": "models",
    "fonts": "fonts",
    "tiktoken": "tiktoken",
    "cmap": "cmap",
}

@dataclass(frozen=True)
class CheckResult:
    """单项自检结果。``detail`` 面向用户展示，不包含凭据。"""

    key: str
    label: str
    status: str
    detail: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
        }
        if self.hint:
            payload["hint"] = self.hint
        return payload


def engine_cache_folder() -> Path:
    """引擎缓存目录；优先使用引擎自己的常量，避免路径写两份。"""
    try:
        from babeldoc.const import CACHE_FOLDER

        return Path(CACHE_FOLDER)
    except Exception:  # pragma: no cover - 仅在引擎不可导入时回退
        return Path.home() / ".cache" / "babeldoc"


def default_app_data_dir() -> Path:
    """应用数据目录：``%LOCALAPPDATA%\\BabelDOC Workbench``。"""
    import os

    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "BabelDOC Workbench"
    return Path.home() / "AppData" / "Local" / "BabelDOC Workbench"


def sha3_256_of_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha3_256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def overall_status(results: Iterable[CheckResult]) -> str:
    states = {item.status for item in results}
    if STATUS_FAIL in states:
        return STATUS_FAIL
    if STATUS_WARN in states:
        return STATUS_WARN
    return STATUS_OK


def check_python_runtime(
    version_info: Sequence[int] | None = None,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
) -> CheckResult:
    info = tuple(version_info or sys.version_info)
    version = ".".join(str(part) for part in info[:3])
    executable = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    mode = "冻结版（PyInstaller）" if frozen else "源码运行"
    if info[:2] < MIN_PYTHON:
        return CheckResult(
            "python_runtime",
            "Python 运行时",
            STATUS_FAIL,
            f"{version}（{mode}）",
            hint=f"需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 及以上",
        )
    if info[:2] >= MAX_PYTHON_EXCLUSIVE:
        return CheckResult(
            "python_runtime",
            "Python 运行时",
            STATUS_FAIL,
            f"{version}（{mode}）",
            hint="引擎要求 Python < 3.14",
        )
    return CheckResult(
        "python_runtime",
        "Python 运行时",
        STATUS_OK,
        f"{version}（{mode}），解释器 {executable}",
    )


def check_platform(
    system: str | None = None,
    machine: str | None = None,
    *,
    pointer_size: int | None = None,
) -> CheckResult:
    import platform as platform_module

    system = system or platform_module.system()
    machine = machine or platform_module.machine()
    # Windows 的 CPython 不提供 SIZEOF_VOID_P，直接用指针宽度判断位数
    bits = struct.calcsize("P") if pointer_size is None else pointer_size
    detail = f"{system} {machine}，指针宽度 {bits} 字节"
    if system.lower() != "windows":
        return CheckResult(
            "platform",
            "运行平台",
            STATUS_WARN,
            detail,
            hint="首版只交付 Windows 10/11 x64",
        )
    if bits != 8:
        return CheckResult(
            "platform",
            "运行平台",
            STATUS_FAIL,
            detail,
            hint="需要 64 位 Python",
        )
    return CheckResult("platform", "运行平台", STATUS_OK, detail)


def check_assets_inventory(
    inventory: Mapping[str, Sequence[Mapping[str, str]]],
    cache_root: Path,
    *,
    verify_hashes: bool = False,
) -> list[CheckResult]:
    """检查模型、字体、tiktoken、cmap 是否齐全；可选校验 sha3_256。"""
    results: list[CheckResult] = []
    for group, subdir in ASSET_SUBDIRS.items():
        entries = list(inventory.get(group) or [])
        if not entries:
            continue
        missing: list[str] = []
        mismatched: list[str] = []
        for entry in entries:
            name = str(entry.get("name", ""))
            path = cache_root / subdir / name
            if not path.is_file() or path.stat().st_size == 0:
                missing.append(name)
                continue
            expected = entry.get("sha3_256")
            if verify_hashes and expected:
                if sha3_256_of_file(path) != expected:
                    mismatched.append(name)
        label = {
            "models": "版面模型",
            "fonts": "字体资源",
            "tiktoken": "tiktoken 缓存",
            "cmap": "CMap 资源",
        }[group]
        key = f"assets_{group}"
        if missing or mismatched:
            detail = (
                f"共 {len(entries)} 项，缺失 {len(missing)} 项，校验不符 {len(mismatched)} 项"
            )
            hint = "在设置中下载资源，或导入离线资源包后重试"
            if missing:
                hint = f"{hint}；缺失示例：{missing[:3]}"
            results.append(CheckResult(key, label, STATUS_FAIL, detail, hint=hint))
            continue
        detail = f"共 {len(entries)} 项齐全" + ("，sha3_256 校验通过" if verify_hashes else "")
        results.append(CheckResult(key, label, STATUS_OK, detail))
    return results


def check_directory_writable(
    path: Path,
    *,
    key: str = "app_data_dir",
    label: str = "应用数据目录",
    create: bool = False,
) -> CheckResult:
    """在不改变目录结构的前提下探测可写性。"""
    target = Path(path)
    probe_dir = target if target.is_dir() else target.parent
    created_note = ""
    if not target.exists():
        created_note = "（尚未创建，检查其父目录）"
    if not probe_dir.is_dir():
        return CheckResult(
            key,
            label,
            STATUS_FAIL,
            f"{target} 不存在且父目录不可用",
            hint="检查路径是否存在、是否有权限",
        )
    probe = probe_dir / f".babeldoc-write-probe-{sysconfig.get_platform()}"
    try:
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult(
            key,
            label,
            STATUS_FAIL,
            f"{target} 不可写：{exc}",
            hint="换一个有写入权限的账号或磁盘后重试",
        )
    if create and not target.exists():
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return CheckResult(
                key,
                label,
                STATUS_FAIL,
                f"{target} 无法创建：{exc}",
            )
    return CheckResult(key, label, STATUS_OK, f"{target} 可写{created_note}")


def check_onnxruntime() -> CheckResult:
    try:
        import onnxruntime
    except Exception as exc:
        return CheckResult(
            "onnxruntime",
            "ONNX Runtime",
            STATUS_FAIL,
            f"导入失败：{type(exc).__name__}: {exc}",
            hint="重新安装依赖，或检查打包时是否收集了 onnxruntime 原生库",
        )
    try:
        providers = list(onnxruntime.get_available_providers())
    except Exception as exc:  # pragma: no cover - 少见
        return CheckResult(
            "onnxruntime",
            "ONNX Runtime",
            STATUS_FAIL,
            f"查询 provider 失败：{type(exc).__name__}: {exc}",
        )
    detail = f"{onnxruntime.__version__}，provider：{', '.join(providers) or '无'}"
    if not providers:
        return CheckResult(
            "onnxruntime",
            "ONNX Runtime",
            STATUS_FAIL,
            detail,
            hint="没有可用执行提供程序，版面模型无法运行",
        )
    return CheckResult("onnxruntime", "ONNX Runtime", STATUS_OK, detail)


def check_webview2() -> CheckResult:
    """检查 WebView2 Runtime 是否已安装（读取注册表，不启动窗口）。"""
    if not sys.platform.startswith("win"):
        return CheckResult(
            "webview2",
            "WebView2 Runtime",
            STATUS_WARN,
            "非 Windows 平台，跳过",
        )
    import winreg

    subkeys = [
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        ),
        (
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        ),
    ]
    for hive, subkey in subkeys:
        try:
            with winreg.OpenKey(hive, subkey) as handle:
                version, _ = winreg.QueryValueEx(handle, "pv")
        except OSError:
            continue
        if str(version).strip():
            return CheckResult(
                "webview2",
                "WebView2 Runtime",
                STATUS_OK,
                f"已安装，版本 {version}",
            )
    return CheckResult(
        "webview2",
        "WebView2 Runtime",
        STATUS_FAIL,
        "未检测到 Evergreen Runtime",
        hint="安装 Microsoft Edge WebView2 Runtime 后重试",
    )


def check_credential_store() -> CheckResult:
    """检查系统凭据服务是否可用；不可用时只警告（用户仍可查看文件，但不能保存 Key）。"""
    try:
        from babeldoc_workbench.security import credentials_available

        available, detail = credentials_available()
    except Exception as exc:  # pragma: no cover - 仅在导入失败时
        return CheckResult(
            "credential_store",
            "系统凭据存储",
            STATUS_WARN,
            f"无法检测：{type(exc).__name__}: {exc}",
            hint="API Key 将无法保存到系统凭据存储",
        )
    if available:
        return CheckResult(
            "credential_store", "系统凭据存储", STATUS_OK, f"可用（{detail}）"
        )
    return CheckResult(
        "credential_store",
        "系统凭据存储",
        STATUS_WARN,
        f"不可用：{detail}",
        hint="保存 API Key 时会报错；请检查系统凭据管理器是否可用，不要改用明文保存",
    )


def _probe_worker_entry(conn) -> None:
    """工作进程探测入口：验证 spawn 后子进程能否导入引擎。"""
    payload: dict[str, Any] = {"ok": False, "detail": "", "error": None}
    try:
        import onnxruntime

        from babeldoc.glossary import Glossary  # noqa: F401 - 导入 hyperscan 原生扩展
        from babeldoc_workbench.engine.adapter import build_config  # noqa: F401

        payload["ok"] = True
        payload["detail"] = f"子进程导入引擎成功，onnxruntime {onnxruntime.__version__}"
    except BaseException as exc:  # noqa: BLE001 - 需要把任何失败都回报给父进程
        payload["error"] = type(exc).__name__
        payload["detail"] = str(exc)
    try:
        conn.send(payload)
    finally:
        conn.close()


def probe_worker_spawn(timeout: float = 120.0) -> CheckResult:
    """真实启动一次 spawn 子进程，确认冻结/源码两种形态下都能导入引擎。"""
    context = multiprocessing.get_context("spawn")
    parent_conn, child_conn = context.Pipe(duplex=False)
    process = context.Process(
        target=_probe_worker_entry, args=(child_conn,), name="babeldoc-selfcheck"
    )
    try:
        process.start()
        child_conn.close()
        if not parent_conn.poll(timeout):
            process.terminate()
            process.join(10)
            return CheckResult(
                "worker_spawn",
                "工作进程",
                STATUS_FAIL,
                f"{timeout:.0f} 秒内没有收到子进程响应",
                hint="检查是否有安全软件拦截子进程，或打包时遗漏了入口模块",
            )
        payload = parent_conn.recv()
    except Exception as exc:  # pragma: no cover - 平台相关
        process.terminate()
        return CheckResult(
            "worker_spawn",
            "工作进程",
            STATUS_FAIL,
            f"启动失败：{type(exc).__name__}: {exc}",
        )
    finally:
        try:
            parent_conn.close()
        except OSError:
            pass
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(10)
        return CheckResult(
            "worker_spawn",
            "工作进程",
            STATUS_FAIL,
            "子进程没有按预期退出",
            hint="检查入口是否调用了 multiprocessing.freeze_support()",
        )
    if not payload.get("ok"):
        return CheckResult(
            "worker_spawn",
            "工作进程",
            STATUS_FAIL,
            f"{payload.get('error')}: {payload.get('detail')}",
            hint="子进程无法导入引擎，通常是打包未收集 babeldoc 或原生依赖",
        )
    return CheckResult("worker_spawn", "工作进程", STATUS_OK, str(payload.get("detail")))


def run_all_checks(
    *,
    deep: bool = False,
    cache_root: Path | None = None,
    app_data_dir: Path | None = None,
    include_worker_probe: bool = True,
) -> list[CheckResult]:
    """执行全部自检项。``deep=True`` 时额外校验资源 sha3_256（耗时较长）。"""
    results: list[CheckResult] = [
        check_python_runtime(),
        check_platform(),
        check_onnxruntime(),
    ]
    root = Path(cache_root) if cache_root is not None else engine_cache_folder()
    try:
        from babeldoc.assets.assets import generate_all_assets_file_list

        inventory = generate_all_assets_file_list()
    except Exception as exc:  # pragma: no cover - 仅在引擎不可导入时
        results.append(
            CheckResult(
                "assets_inventory",
                "资源清单",
                STATUS_FAIL,
                f"无法读取引擎资源清单：{type(exc).__name__}: {exc}",
            )
        )
    else:
        results.extend(
            check_assets_inventory(inventory, root, verify_hashes=deep)
        )
    results.append(
        check_directory_writable(
            Path(app_data_dir) if app_data_dir is not None else default_app_data_dir(),
            key="app_data_dir",
            label="应用数据目录",
        )
    )
    results.append(check_webview2())
    results.append(check_credential_store())
    if include_worker_probe:
        results.append(probe_worker_spawn())
    return results


def run_light_checks(
    *, cache_root: Path | None = None, app_data_dir: Path | None = None
) -> list[CheckResult]:
    """轻量检查：不启动子进程、不做磁盘哈希，用于 ``/api/health`` 的资源状态摘要。"""
    return run_all_checks(
        deep=False,
        cache_root=cache_root,
        app_data_dir=app_data_dir,
        include_worker_probe=False,
    )


def format_report(results: Sequence[CheckResult]) -> str:
    icons = {STATUS_OK: "✅", STATUS_WARN: "⚠️", STATUS_FAIL: "❌"}
    lines = ["BabelDOC 工作台环境自检", "-" * 60]
    for item in results:
        lines.append(f"{icons.get(item.status, '?')} {item.label}：{item.detail}")
        if item.hint:
            lines.append(f"   → {item.hint}")
    lines.append("-" * 60)
    overall = overall_status(results)
    lines.append(f"总体结论：{icons.get(overall, '?')} {overall}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="BabelDOC 工作台环境自检")
    parser.add_argument("--deep", action="store_true", help="额外校验资源 sha3_256")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument(
        "--skip-worker-probe", action="store_true", help="跳过子进程启动检查"
    )
    args = parser.parse_args(argv)

    results = run_all_checks(
        deep=args.deep, include_worker_probe=not args.skip_worker_probe
    )
    if args.json:
        print(
            json.dumps(
                {
                    "overall": overall_status(results),
                    "checks": [item.to_dict() for item in results],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_report(results))
    return 0 if overall_status(results) != STATUS_FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
