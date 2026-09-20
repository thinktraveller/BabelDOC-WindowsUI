"""应用入口：冻结后子进程会再次进入本入口，因此必须先调用 freeze_support()。"""

from __future__ import annotations

import os
import multiprocessing
import sys

CRASH_REPORT_NAME = "babeldoc-crash-report.txt"
STARTUP_MARKER_NAME = "startup-marker.log"


def _app_logs_dir():
    """返回应用日志目录；拿不到系统路径时返回 None。"""
    from pathlib import Path

    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return Path(base) / "BabelDOC Workbench" / "logs"


def write_startup_marker() -> None:
    """在导入第三方库之前留下启动痕迹。

    双击启动失败时用户看不到任何输出，这个文件用于区分「进程根本没起来」与
    「进程起来了但窗口没显示」，也用于核对启动时的标准流状态。
    """
    import time

    logs = _app_logs_dir()
    if logs is None:
        return
    try:
        logs.mkdir(parents=True, exist_ok=True)
        line = (
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} 启动 marker "
            f"冻结={bool(getattr(sys, 'frozen', False))} "
            f"argv={list(sys.argv[1:])} "
            f"stdout={'None' if sys.stdout is None else 'ok'} "
            f"stderr={'None' if sys.stderr is None else 'ok'}\n"
        )
        with (logs / STARTUP_MARKER_NAME).open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        return


def write_crash_report(exc: BaseException) -> str:
    """把启动期崩溃写入文件：无控制台程序默认看不到 traceback。"""
    import tempfile
    import traceback
    from pathlib import Path

    path = Path(tempfile.gettempdir()) / CRASH_REPORT_NAME
    text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        path = None

    # 同时写进应用日志目录，避免用户只在一处找证据
    logs = _app_logs_dir()
    if logs is not None:
        try:
            logs.mkdir(parents=True, exist_ok=True)
            with (logs / "startup-crash.log").open("a", encoding="utf-8") as handle:
                handle.write(text)
                handle.write("\n")
        except OSError:
            pass

    return str(path) if path is not None else ""


def ensure_console_streams() -> None:
    """冻结为无控制台程序时，附着到父进程控制台，让命令行验证能看到输出。

    正式运行（双击）不会附着任何控制台，行为不受影响。
    """
    if not getattr(sys, "frozen", False):
        return

    if sys.platform.startswith("win") and (sys.stdout is None or sys.stderr is None):
        try:
            import ctypes

            # 从终端运行时父进程有控制台，附着后命令行验证能看到输出
            if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
                if sys.stdout is None:
                    sys.stdout = open(  # noqa: SIM115 - 保持打开直到进程结束
                        "CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace"
                    )
                if sys.stderr is None:
                    sys.stderr = open(  # noqa: SIM115
                        "CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace"
                    )
        except Exception:
            pass

    # 双击启动时父进程（资源管理器）没有控制台可附着，标准流会一直是 None。
    # 第三方库会调用 sys.stdout.isatty()（例如 uvicorn 的日志配置），必须兜底，
    # 否则进程会在建窗口之前直接崩溃，表现为「点击无反应」。
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(
                    sys,
                    name,
                    open(  # noqa: SIM115 - 保持打开直到进程结束
                        os.devnull, "w", encoding="utf-8", errors="replace"
                    ),
                )
            except OSError:
                pass
    if sys.stdin is None:
        try:
            sys.stdin = open(os.devnull, "r", encoding="utf-8", errors="replace")
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    # 必须最先调用：冻结后 multiprocessing 会以特殊参数重启本入口
    multiprocessing.freeze_support()
    ensure_console_streams()
    write_startup_marker()
    try:
        from babeldoc_workbench.app import run_app

        return run_app(sys.argv[1:] if argv is None else argv)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - 启动期崩溃需要留证据
        write_crash_report(exc)
        if sys.stderr is not None:
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
