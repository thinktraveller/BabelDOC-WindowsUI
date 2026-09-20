"""应用服务装配：本地回环服务、会话令牌与来源校验、自检页面与桌面窗口。

安全边界（计划书步骤 3）：

- 只监听 ``127.0.0.1``，端口由系统分配，不固定端口；
- 每次启动生成一次性会话令牌，接口要求请求头 ``X-Workbench-Token``；
- 校验 ``Host`` 与 ``Origin``，两者都必须是本机回环来源；
- 令牌只注入窗口内存，不写进 URL、日志或磁盘。
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import tempfile
import threading
import time
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from babeldoc_workbench import __version__ as APP_VERSION
from babeldoc_workbench import security
from babeldoc_workbench.engine.selfcheck import (
    STATUS_FAIL,
    CheckResult,
    format_report,
    overall_status,
    run_all_checks,
    run_light_checks,
)
from babeldoc_workbench.logging_setup import setup_logging
from babeldoc_workbench.settings import (
    AppDataDirectoryError,
    AppPaths,
    ensure_app_dirs,
)
from babeldoc_workbench.single_instance import SingleInstance, activate_existing_window

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
WINDOW_TITLE = "BabelDOC 工作台"
SELF_CHECK_TITLE = "BabelDOC 工作台 · 环境自检"
SELF_CHECK_REPORT_NAME = "babeldoc-selfcheck-report.txt"
TOKEN_EXEMPT_PATHS = frozenset({"/", "/favicon.ico", "/selfcheck"})
TOKEN_EXEMPT_PREFIXES = ("/assets/",)
FRONTEND_DIST_ENV = "BABELDOC_FRONTEND_DIST"


def frontend_dist_dir() -> Path | None:
    """定位前端构建产物目录；未构建时返回 None（界面回退到自检页）。"""
    candidates: list[Path] = []
    override = os.environ.get(FRONTEND_DIST_ENV)
    if override and override.strip():
        candidates.append(Path(override.strip()))
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.append(bundle_root / "frontend_dist")
        candidates.append(Path(sys.executable).parent / "frontend_dist")
    else:
        candidates.append(Path(__file__).resolve().parents[2] / "frontend" / "dist")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


def engine_version() -> str:
    try:
        from babeldoc.const import __version__ as version

        return str(version)
    except Exception:  # pragma: no cover - 引擎不可导入时
        return "unknown"


@dataclass
class SessionInfo:
    """一次运行的会话信息；``token`` 不进入日志、URL 与磁盘。"""

    token: str
    port: int = 0
    app_version: str = APP_VERSION
    engine_version: str = field(default_factory=engine_version)
    started_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    def public_dict(self) -> dict:
        return {
            "app_version": self.app_version,
            "engine_version": self.engine_version,
            "port": self.port,
            "started_at": self.started_at,
            "token_required": True,
            "token_header": security.TOKEN_HEADER,
        }


class DesktopBridge:
    """暴露给页面的最小桌面桥：只提供目录选择（另存成果用）。"""

    def __init__(self) -> None:
        self.window = None
        self.allow_close = None

    def choose_directory(self, initial: str | None = None) -> str | None:
        try:
            import webview
        except Exception:  # pragma: no cover - 浏览器模式下没有 pywebview
            return None
        if self.window is None:
            return None
        try:
            result = self.window.create_file_dialog(
                webview.FOLDER_DIALOG, directory=initial or ""
            )
        except Exception as exc:  # pragma: no cover - 对话框异常
            logger.warning("选择目录失败：%s", exc)
            return None
        if not result:
            return None
        if isinstance(result, (list, tuple)):
            return str(result[0]) if result else None
        return str(result)

    def finish_close(self) -> bool:
        """页面已完成关闭选择，允许窗口真正关闭。"""
        if self.allow_close is not None:
            self.allow_close()
        if self.window is not None:
            self.window.destroy()
        return True


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
  <div id="summary" class="warn">正在检查…</div>
  <table>
    <thead><tr><th style="width:150px">检查项</th><th style="width:90px">状态</th><th>详情</th></tr></thead>
    <tbody id="rows"><tr><td colspan="3">加载中…</td></tr></tbody>
  </table>
  <div id="error"></div>
  <script>
    const icons = { ok: "✅ 通过", warn: "⚠️ 注意", fail: "❌ 失败" };
    function headers() {
      const token = window.__WORKBENCH_TOKEN__;
      return token ? { "X-Workbench-Token": token } : {};
    }
    async function load(attempt = 0) {
      if (!window.__WORKBENCH_TOKEN__ && attempt < 40) {
        document.getElementById("summary").textContent = "等待会话令牌…";
        setTimeout(() => load(attempt + 1), 250);
        return;
      }
      try {
        const response = await fetch("/api/selfcheck", { headers: headers() });
        if (!response.ok) {
          throw new Error("HTTP " + response.status + " " + (await response.text()));
        }
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
    """自检结果按 ``deep`` 缓存一次，避免页面轮询重复启动子进程。"""
    cache: dict[bool, list[CheckResult]] = {}
    lock = threading.Lock()

    def provider(deep: bool = False) -> list[CheckResult]:
        with lock:
            if deep not in cache:
                cache[deep] = run_all_checks(deep=deep)
            return cache[deep]

    return provider


def _cache_light_checks() -> Callable[[], list[CheckResult]]:
    cache: list[list[CheckResult]] = []
    lock = threading.Lock()

    def provider() -> list[CheckResult]:
        with lock:
            if not cache:
                cache.append(run_light_checks())
            return cache[0]

    return provider


def create_app(
    checks_provider: Callable[[bool], list[CheckResult]],
    session: SessionInfo,
    light_checks_provider: Callable[[], list[CheckResult]] | None = None,
    frontend_dir: Path | None = None,
):
    """构造工作台服务：先做 Host/Origin/令牌校验，再进入路由。"""
    from fastapi import FastAPI, Request
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import HTMLResponse, JSONResponse

    from babeldoc_workbench.api.settings import router as settings_router
    from babeldoc_workbench.api.files import router as files_router
    from babeldoc_workbench.api.tasks import router as tasks_router
    from babeldoc_workbench.api.glossary import router as glossary_router
    from babeldoc_workbench.api.app_control import router as app_router

    app = FastAPI(title="BabelDOC Workbench", docs_url=None, redoc_url=None)
    light_provider = light_checks_provider or _cache_light_checks()
    app.include_router(settings_router)
    app.include_router(files_router)
    app.include_router(tasks_router)
    app.include_router(glossary_router)
    app.include_router(app_router)

    dist_dir = Path(frontend_dir) if frontend_dir is not None else frontend_dist_dir()
    if dist_dir is not None and not (dist_dir / "index.html").is_file():
        # 前端尚未构建：回退到内置自检页面，而不是返回 500
        dist_dir = None
    if dist_dir is not None and (dist_dir / "assets").is_dir():
        app.mount(
            "/assets", StaticFiles(directory=str(dist_dir / "assets")), name="assets"
        )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not security.check_host(request.headers.get("host"), session.port):
            logger.warning("拒绝请求：Host=%s", request.headers.get("host"))
            return JSONResponse(status_code=403, content={"detail": "非法的 Host 头"})
        if not security.check_origin(request.headers.get("origin"), session.port):
            logger.warning("拒绝请求：Origin=%s", request.headers.get("origin"))
            return JSONResponse(status_code=403, content={"detail": "请求来源不被允许"})
        path = request.url.path
        exempt = path in TOKEN_EXEMPT_PATHS or path.startswith(TOKEN_EXEMPT_PREFIXES)
        if not exempt:
            token = request.headers.get(security.TOKEN_HEADER)
            if not security.tokens_equal(token, session.token):
                logger.warning("拒绝请求：%s 缺少或携带无效会话令牌", path)
                return JSONResponse(
                    status_code=403, content={"detail": "缺少或无效的会话令牌"}
                )
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        if dist_dir is not None:
            return (dist_dir / "index.html").read_text(encoding="utf-8")
        return INDEX_HTML

    @app.get("/selfcheck", response_class=HTMLResponse)
    def selfcheck_page() -> str:
        return INDEX_HTML

    @app.get("/api/health")
    def health() -> dict:
        results = light_provider()
        summary = {item.key: item.status for item in results}
        return {
            "status": "ok" if STATUS_FAIL not in summary.values() else "degraded",
            "app_version": session.app_version,
            "engine_version": session.engine_version,
            "resources": summary,
            "checks": [item.to_dict() for item in results],
        }

    @app.get("/api/session")
    def session_info() -> dict:
        return session.public_dict()

    @app.get("/api/selfcheck")
    def selfcheck(deep: bool = False) -> dict:
        results = checks_provider(deep)
        return {
            "overall": overall_status(results),
            "checks": [item.to_dict() for item in results],
        }

    return app


def bind_loopback_socket(port: int = 0) -> tuple[socket.socket, int]:
    """绑定回环端口；默认 0 表示由系统分配空闲端口。

    固定端口只用于开发调试（Vite 代理需要已知端口），产品行为始终是随机端口。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, int(port)))
    return sock, int(sock.getsockname()[1])


def start_server(app, sock: socket.socket):
    """在后台线程用已绑定的套接字启动服务。"""
    import uvicorn

    config = uvicorn.Config(app, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, name="babeldoc-http", daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            break
        time.sleep(0.05)
    return server


def serve_in_background(app) -> tuple[object, int]:
    """便捷入口：绑定端口并启动服务（供脚本与测试使用）。"""
    sock, port = bind_loopback_socket()
    return start_server(app, sock), port


def emit_report(text: str, report_file: str | None = None) -> str:
    """输出报告；无控制台时（双击或 GUI 子系统）改为写报告文件。"""
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


def run_app(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="BabelDOC", description="BabelDOC 本地翻译工作台"
    )
    parser.add_argument("--self-check", action="store_true", help="只运行环境自检并退出")
    parser.add_argument("--json", action="store_true", help="自检结果以 JSON 输出")
    parser.add_argument("--deep", action="store_true", help="自检时校验资源 sha3_256")
    parser.add_argument(
        "--skip-worker-probe", action="store_true", help="自检时跳过子进程启动检查"
    )
    parser.add_argument(
        "--no-window", action="store_true", help="只启动本地服务，不打开窗口"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="本地服务端口；默认 0 表示由系统分配（仅开发调试时指定固定端口）",
    )
    parser.add_argument(
        "--report-file", default=None, help="把报告写入指定文件（便于脚本读取）"
    )
    parser.add_argument(
        "--verify-job", default=None, help="打包验证：对给定 PDF 跑一次离线翻译"
    )
    parser.add_argument("--verify-output", default=None, help="--verify-job 的输出目录")
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

    # 应用数据目录：不可写时明确报错，不退化为临时目录
    try:
        paths = ensure_app_dirs()
    except AppDataDirectoryError as exc:
        emit_report(f"❌ {exc}", args.report_file)
        return 2
    from babeldoc_workbench import db

    db.init(paths.db)
    from babeldoc_workbench.settings import set_current_paths

    set_current_paths(paths)
    log_file = setup_logging(paths.logs)
    logger.info("应用启动：数据目录 %s，日志 %s", paths.root, log_file)
    from babeldoc_workbench.services import recovery

    recovered = recovery.recover_on_startup()
    if recovered["interrupted_tasks"]:
        logger.warning(
            "启动恢复：%s 个任务被标记为 interrupted，%s 个残留工作进程被清理",
            len(recovered["interrupted_tasks"]),
            len(recovered["orphan_workers"]),
        )

    # 单实例：已有实例时不再开第二个窗口
    lock = SingleInstance(lock_dir=paths.tmp)
    if not lock.acquire():
        activated = activate_existing_window(SELF_CHECK_TITLE) or activate_existing_window(
            WINDOW_TITLE
        )
        message = "检测到已在运行的实例。"
        message += "已尝试激活已有窗口。" if activated else "请查看已打开的窗口。"
        logger.info(message)
        emit_report(message, args.report_file)
        return 0

    from babeldoc_workbench.api.tasks import set_queue
    from babeldoc_workbench.services.task_queue import TaskQueue

    queue = TaskQueue()
    queue.start()
    set_queue(queue)

    try:
        session = SessionInfo(token=security.generate_session_token())
        sock, port = bind_loopback_socket(args.port)
        session.port = port
        app = create_app(_cache_checks(), session)
        server = start_server(app, sock)
        url = f"http://{HOST}:{port}/"
        logger.info("本地服务已启动：%s", url)

        if args.no_window:
            # 仅开发/自动验证模式会把端口与令牌打到控制台；日志文件中不记录令牌
            print(f"url={url}")
            print(f"token={session.token}")
            print(f"app_dir={paths.root}")
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
            finally:
                server.should_exit = True
            return 0

        import webview

        bridge = DesktopBridge()
        window = webview.create_window(SELF_CHECK_TITLE, url, js_api=bridge)
        bridge.window = window
        closing_state = {"decided": False}

        def _allow_close() -> None:
            closing_state["decided"] = True

        bridge.allow_close = _allow_close

        def on_closing() -> bool:
            """有任务在运行时先让界面询问用户，不静默杀进程。"""
            if closing_state["decided"]:
                return True
            try:
                active = recovery.active_tasks()
            except Exception:  # pragma: no cover - 数据库异常时直接关闭
                return True
            if not active:
                return True
            try:
                window.evaluate_js(
                    "window.__WORKBENCH_ON_CLOSE__ && window.__WORKBENCH_ON_CLOSE__();"
                )
            except Exception:  # pragma: no cover - 页面不可用时直接关闭
                logger.warning("无法向页面发送关闭询问，直接关闭窗口")
                return True
            logger.info("有 %s 个任务在运行，等待界面确认关闭方式", len(active))
            return False

        window.events.closing += on_closing

        def inject_token() -> None:
            """把令牌注入页面内存（不经过 URL、不落盘）。"""
            payload = json.dumps(session.token)
            for _ in range(40):
                time.sleep(0.25)
                try:
                    window.evaluate_js(f"window.__WORKBENCH_TOKEN__ = {payload};")
                    return
                except Exception:
                    continue
            logger.warning("未能把会话令牌注入页面")

        webview.start(inject_token)
        server.should_exit = True
        return 0
    finally:
        set_queue(None)
        queue.stop()
        lock.release()


def run_verify_job(args: argparse.Namespace) -> int:
    """打包验证：在工作进程里对给定 PDF 跑一次离线翻译（跳过 LLM，不需要 Key）。"""
    import multiprocessing

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
    output_dir = Path(
        args.verify_output or (Path(tempfile.gettempdir()) / "babeldoc-verify-job")
    )
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
        missing = [name for name, path in produced if path and not Path(path).is_file()]
        for name, path in produced:
            if path:
                exists = Path(path).is_file()
                lines.append(f"{'✅' if exists else '❌'} {name}：{path}")
        ok = not missing
        if ok:
            lines.append("✅ 端到端（离线，跳过 LLM）通过")
    else:
        lines.append("❌ 没有收到 finish 事件")
    lines.append("-" * 60)
    lines.append(f"总体结论：{'✅ ok' if ok else '❌ fail'}")
    emit_report("\n".join(lines), args.report_file)
    return 0 if ok else 1
