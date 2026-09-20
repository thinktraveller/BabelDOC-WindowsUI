"""最小应用装配：环境自检服务 + 桌面窗口（步骤 2 范围）。

本步只解决“冻结后能否启动、能否加载原生依赖、能否用 spawn 起工作进程”，因此：

- 服务只监听回环地址，端口由系统分配；
- 页面只渲染环境自检结果；
- 会话令牌、来源校验、单实例锁、应用目录与凭据管理属于步骤 3，本步不实现。
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from babeldoc_workbench.engine.selfcheck import (
    STATUS_FAIL,
    CheckResult,
    format_report,
    overall_status,
    run_all_checks,
)

HOST = "127.0.0.1"
WINDOW_TITLE = "BabelDOC 工作台 · 环境自检"
SELF_CHECK_REPORT_NAME = "babeldoc-selfcheck-report.txt"

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>BabelDOC 工作台 · 环境自检</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: "Microsoft YaHei", system-ui, sans-serif; margin: 0; padding: 24px 28px; }
    h1 { font-size: 20px; margin: 0 0 4px; }
    p.sub { margin: 0 0 18px; color: #6b7280; font-size: 13px; }
    #summary { display: inline-block; padding: 4px 12px; border-radius: 999px; font-size: 13px; margin-bottom: 16px; }
    .ok { background: #dcfce7; color: #15803d; }
    .warn { background: #fef3c7; color: #b45309; }
    .fail { background: #fee2e2; color: #b91c1c; }
    table { border-collapse: collapse; width: 100%; font-size: 14px; }
    th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
    th { font-weight: 600; color: #374151; background: rgba(127,127,127,.08); }
    td.hint { color: #6b7280; font-size: 13px; }
    .status { white-space: nowrap; }
    #error { color: #b91c1c; font-size: 13px; margin-top: 12px; }
  </style>
</head>
<body>
  <h1>环境自检</h1>
  <p class="sub">检查项来自工作进程实测：运行时、ONNX Runtime、模型与字体资源、子进程启动、WebView2 与数据目录。</p>
  <div id="summary" class="warn">正在检查…（首次检查需数秒）</div>
  <table>
    <thead><tr><th style="width:150px">检查项</th><th style="width:80px">状态</th><th>详情</th></tr></thead>
    <tbody id="rows"><tr><td colspan="3">加载中…</td></tr></tbody>
  </table>
  <div id="error"></div>
  <script>
    const icons = { ok: "✅ 通过", warn: "⚠️ 注意", fail: "❌ 失败" };
    async function load() {
      try {
        const response = await fetch("/api/selfcheck");
        const data = await response.json();
        const summary = document.getElementById("summary");
        summary.className = data.overall;
        summary.textContent = "总体结论：" + (icons[data.overall] || data.overall);
        document.getElementById("rows").innerHTML = data.checks.map(item => `
          <tr>
            <td>${item.label}</td>
            <td class="status">${icons[item.status] || item.status}</td>
            <td>${item.detail}${item.hint ? `<div class="hint">→ ${item.hint}</div>` : ""}</td>
          </tr>`).join("");
        document.getElementById("error").textContent = "";
      } catch (err) {
        document.getElementById("error").textContent = "读取自检结果失败：" + err;
      }
    }
    load();
  </script>
</body>
</html>
"""


def _cache_checks() -> Callable[[bool], list[CheckResult]]:
    """自检结果按``deep``缓存一次，避免页面轮询重复启动子进程。"""
    cache: dict[bool, list[CheckResult]] = {}
    lock = threading.Lock()

    def provider(deep: bool = False) -> list[CheckResult]:
        with lock:
            if deep not in cache:
                cache[deep] = run_all_checks(deep=deep)
            return cache[deep]

    return provider


def create_app(checks_provider: Callable[[bool], list[CheckResult]]):
    """构造只提供自检页面的最小服务（步骤 3 会在此基础上扩展）。"""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    app = FastAPI(title="BabelDOC Workbench Self-Check", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.get("/api/selfcheck")
    def selfcheck(deep: bool = False) -> dict:
        results = checks_provider(deep)
        return {
            "overall": overall_status(results),
            "checks": [item.to_dict() for item in results],
        }

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


def serve_in_background(app) -> tuple[object, int]:
    """在后台线程启动服务；端口由系统分配，只监听回环地址。"""
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    port = int(sock.getsockname()[1])
    config = uvicorn.Config(app, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        name="babeldoc-http",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            break
        time.sleep(0.05)
    return server, port


def run_self_check(args: argparse.Namespace) -> int:
    results = run_all_checks(
        deep=args.deep, include_worker_probe=not args.skip_worker_probe
    )
    overall = overall_status(results)
    if args.json:
        text = json.dumps(
            {"overall": overall, "checks": [item.to_dict() for item in results]},
            ensure_ascii=False,
            indent=2,
        )
    else:
        text = format_report(results)
    emit_report(text, args.report_file)
    return 1 if overall == STATUS_FAIL else 0


def emit_report(text: str, report_file: str | None = None) -> str:
    """输出自检报告；无控制台时（双击或 GUI 子系统）改为写报告文件。

    ``report_file`` 用于自动验证：指定后总是写文件，便于脚本读取。
    """
    if report_file:
        try:
            Path(report_file).write_text(text, encoding="utf-8")
            return report_file
        except OSError:
            pass
    if sys.stdout is not None:
        print(text)
        return ""
    report_path = Path(tempfile.gettempdir()) / SELF_CHECK_REPORT_NAME
    try:
        report_path.write_text(text, encoding="utf-8")
    except OSError:
        return ""
    return str(report_path)


def run_app(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="BabelDOC", description="BabelDOC 本地翻译工作台")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="只运行环境自检并退出（打包验证使用）",
    )
    parser.add_argument("--json", action="store_true", help="自检结果以 JSON 输出")
    parser.add_argument("--deep", action="store_true", help="自检时校验资源 sha3_256")
    parser.add_argument(
        "--report-file",
        default=None,
        help="把自检报告写入指定文件（无控制台环境下便于脚本读取）",
    )
    parser.add_argument(
        "--skip-worker-probe", action="store_true", help="自检时跳过子进程启动检查"
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="只启动本地服务，不打开窗口（开发与自动验证使用）",
    )
    parser.add_argument(
        "--verify-job",
        default=None,
        help="开发/打包验证：在工作进程里跑一次离线翻译（跳过 LLM），参数为输入 PDF",
    )
    parser.add_argument(
        "--verify-output",
        default=None,
        help="与 --verify-job 搭配的输出目录",
    )
    parser.add_argument("--lang-in", default="en", help="验证任务的源语言")
    parser.add_argument("--lang-out", default="zh", help="验证任务的目标语言")
    parser.add_argument(
        "--verify-timeout", type=float, default=1800.0, help="验证任务总超时秒数"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.self_check:
        return run_self_check(args)
    if args.verify_job:
        return run_verify_job(args)

    checks_provider = _cache_checks()
    app = create_app(checks_provider)
    server, port = serve_in_background(app)
    url = f"http://{HOST}:{port}/"
    print(f"自检服务已启动：{url}")

    if args.no_window:
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("收到中断，正在退出")
        finally:
            server.should_exit = True
        return 0

    import webview

    webview.create_window(WINDOW_TITLE, url)
    webview.start()
    server.should_exit = True
    return 0


def run_verify_job(args: argparse.Namespace) -> int:
    """打包验证：在工作进程里对给定 PDF 跑一次离线翻译，检查产物是否落盘。

    该模式只用于验证“冻结后引擎能否真正产出文件”，跳过 LLM 翻译（不需要 API Key）。
    """
    import multiprocessing
    import time

    from babeldoc_workbench.engine.protocol import (
        TERMINAL_EVENT_TYPES,
        EngineApiConfig,
        EngineJobRequest,
    )
    from babeldoc_workbench.engine.worker import MSG_EVENT, MSG_EXIT, worker_entry

    input_path = Path(args.verify_job).resolve()
    if not input_path.is_file():
        emit_report(f"输入文件不存在：{input_path}", args.report_file)
        return 2
    output_dir = Path(args.verify_output or (Path(tempfile.gettempdir()) / "babeldoc-verify-job"))
    output_dir.mkdir(parents=True, exist_ok=True)

    request = EngineJobRequest(
        job_id=f"verify-{int(time.time())}",
        input_path=str(input_path),
        output_dir=str(output_dir),
        lang_in=args.lang_in,
        lang_out=args.lang_out,
        skip_translation=True,
        auto_extract_glossary=False,
    )
    api = EngineApiConfig(model="offline-stub")

    context = multiprocessing.get_context("spawn")
    parent_conn, child_conn = context.Pipe(duplex=True)
    process = context.Process(
        target=worker_entry,
        args=(child_conn, request.to_dict(), api.to_dict()),
        name="babeldoc-verify-job",
    )
    process.start()
    child_conn.close()

    finish_payload: dict | None = None
    error_payload: dict | None = None
    stage_line = ""
    started = time.monotonic()
    while True:
        if parent_conn.poll(0.2):
            try:
                message = parent_conn.recv()
            except EOFError:
                break
            if not isinstance(message, dict):
                continue
            if message.get("type") == MSG_EXIT:
                break
            if message.get("type") != MSG_EVENT:
                continue
            event = message.get("event") or {}
            payload = event.get("payload") or {}
            if event.get("type") in TERMINAL_EVENT_TYPES:
                if event.get("type") == "finish":
                    finish_payload = payload
                elif event.get("type") == "error":
                    error_payload = payload
            elif isinstance(payload.get("overall_progress"), (int, float)):
                stage_line = (
                    f"最后阶段 {payload.get('stage_label')}，"
                    f"总进度 {payload['overall_progress']:.1f}%"
                )
        elif not process.is_alive() and not parent_conn.poll():
            break
        if time.monotonic() - started > args.verify_timeout:
            process.terminate()
            emit_report("❌ 验证任务超时", args.report_file)
            return 1
    process.join(30)

    lines = [
        f"BabelDOC 离线翻译验证（{'冻结版' if getattr(sys, 'frozen', False) else '源码运行'}）",
        "-" * 60,
        f"输入：{input_path}",
        f"输出目录：{output_dir}",
        f"工作进程退出码：{process.exitcode}",
        stage_line or "（未收到进度事件）",
    ]
    ok = False
    if error_payload:
        lines.append(f"❌ 引擎错误：{error_payload}")
    elif finish_payload:
        produced = [
            ("单语 PDF", finish_payload.get("mono_pdf_path")),
            ("双语 PDF", finish_payload.get("dual_pdf_path")),
        ]
        missing = [
            name for name, path in produced if path and not Path(path).is_file()
        ]
        for name, path in produced:
            if path:
                exists = "存在" if Path(path).is_file() else "缺失"
                lines.append(f"{'✅' if exists == '存在' else '❌'} {name}：{path}（{exists}）")
        ok = not missing
        if ok:
            lines.append("✅ 端到端（离线，跳过 LLM）通过")
    else:
        lines.append("❌ 没有收到 finish 事件")
    lines.append("-" * 60)
    lines.append(f"总体结论：{'✅ ok' if ok else '❌ fail'}")
    emit_report("\n".join(lines), args.report_file)
    return 0 if ok else 1
