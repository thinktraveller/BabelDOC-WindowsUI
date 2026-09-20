"""冻结版双击启动的回归测试。

用户实际报告过「双击 BabelDOC.exe 无反应」：双击启动的进程没有控制台，
``sys.stdout`` 为 ``None``，uvicorn 配置日志时调用 ``sys.stdout.isatty()``
抛异常，进程在建窗口之前就退出。这些测试锁定当时的崩溃条件。
"""

from __future__ import annotations

import sys

import pytest

from babeldoc_workbench import main as app_main


def test_ensure_console_streams_recovers_from_none(monkeypatch):
    """stdout/stderr 为 None 时必须兜底成可用流，否则第三方库会崩。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdin", None)

    app_main.ensure_console_streams()

    assert sys.stdout is not None
    assert sys.stderr is not None
    assert sys.stdin is not None
    # 触发原始缺陷的调用：必须有 isatty()，且返回值是布尔。
    # 从终端运行时会附着到父控制台（isatty 为 True），双击时为 False，
    # 两者都不再抛 AttributeError。
    assert isinstance(sys.stdout.isatty(), bool)
    assert isinstance(sys.stderr.isatty(), bool)
    # 写入不能抛异常（此前会 AttributeError: 'NoneType'）
    sys.stdout.write("probe\n")
    sys.stderr.write("probe\n")
    sys.stdout.flush()


def test_ensure_console_streams_keeps_working_streams(monkeypatch):
    """已经有标准流时不应替换，命令行运行的输出保持原样。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    original_out = sys.stdout
    original_err = sys.stderr

    app_main.ensure_console_streams()

    assert sys.stdout is original_out
    assert sys.stderr is original_err


def test_ensure_console_streams_ignores_non_frozen(monkeypatch):
    """源码运行（非冻结）不改动标准流，避免影响开发与测试。"""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "stdout", None)

    app_main.ensure_console_streams()

    assert sys.stdout is None


def test_write_startup_marker_records_stdout_state(tmp_path, monkeypatch):
    """启动标记要能区分「进程没起来」与「起来了但窗口没显示」。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["BabelDOC.exe", "--no-window"])

    app_main.write_startup_marker()

    marker = tmp_path / "BabelDOC Workbench" / "logs" / "startup-marker.log"
    assert marker.exists()
    text = marker.read_text(encoding="utf-8")
    assert "启动 marker" in text
    assert "--no-window" in text
    assert "argv=" in text


def test_write_startup_marker_survives_missing_localappdata(monkeypatch):
    """拿不到用户目录时静默跳过，不能让启动流程失败。"""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    app_main.write_startup_marker()


def test_write_crash_report_also_writes_app_log(tmp_path, monkeypatch):
    """崩溃除了写临时目录，也要写进应用日志目录，方便用户取证。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    try:
        raise RuntimeError("模拟启动崩溃")
    except RuntimeError as exc:
        path = app_main.write_crash_report(exc)

    assert path
    from pathlib import Path

    assert "模拟启动崩溃" in Path(path).read_text(encoding="utf-8")
    crash_log = tmp_path / "BabelDOC Workbench" / "logs" / "startup-crash.log"
    assert crash_log.exists()
    assert "模拟启动崩溃" in crash_log.read_text(encoding="utf-8")


def test_start_server_disables_uvicorn_log_config(monkeypatch):
    """uvicorn 自带的日志配置依赖 stdout.isatty()，必须显式关掉。"""
    import socket

    from babeldoc_workbench import app as app_module

    captured: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    class FakeServer:
        started = True

        def __init__(self, config):
            self.config = config

        def run(self, **kwargs):
            return None

    import uvicorn

    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    try:
        app_module.start_server(object(), sock)
    finally:
        sock.close()

    assert captured.get("log_config") is None
