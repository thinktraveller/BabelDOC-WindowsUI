"""应用入口：冻结后子进程会再次进入本入口，因此必须先调用 freeze_support()。"""

from __future__ import annotations

import multiprocessing
import sys

CRASH_REPORT_NAME = "babeldoc-crash-report.txt"


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
        return ""
    return str(path)


def ensure_console_streams() -> None:
    """冻结为无控制台程序时，附着到父进程控制台，让命令行验证能看到输出。

    正式运行（双击）不会附着任何控制台，行为不受影响。
    """
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            return
        sys.stdout = open(  # noqa: SIM115 - 需要保持打开直到进程结束
            "CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace"
        )
        sys.stderr = open(  # noqa: SIM115
            "CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace"
        )
    except Exception:
        # 附着失败不影响功能，调用方会改用报告文件
        return


def main(argv: list[str] | None = None) -> int:
    # 必须最先调用：冻结后 multiprocessing 会以特殊参数重启本入口
    multiprocessing.freeze_support()
    ensure_console_streams()
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
