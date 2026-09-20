"""步骤 1 的开发用冒烟驱动：不打包、不含界面，直接验证工作进程与引擎事件流。

用法（在已激活的虚拟环境中执行）::

    # 离线自检：跳过 LLM 翻译，验证工作进程、ONNX 版面模型、事件流与产物
    python desktop\\tests\\smoke_engine.py --offline

    # 真实翻译：从 .env 读取 API 配置（进程环境变量优先），密钥不出现在命令行
    Copy-Item env.example .env      # 然后填写 BABELDOC_BASE_URL / BABELDOC_MODEL / BABELDOC_API_KEY
    python desktop\\tests\\smoke_engine.py --input examples\\ci\\test.pdf --lang-out zh
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "desktop" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from babeldoc_workbench.engine.protocol import (  # noqa: E402
    EngineApiConfig,
    EngineJobRequest,
)
from babeldoc_workbench.engine.worker import (  # noqa: E402
    CONTROL_CANCEL,
    MSG_CONTROL,
    MSG_EVENT,
    MSG_EXIT,
    worker_entry,
)
from babeldoc_workbench.dev_env import (  # noqa: E402
    DEFAULT_ENV_FILENAME,
    describe_source,
    load_env_file,
    resolve,
)

CTX = multiprocessing.get_context("spawn")

# 变量命名以计划书「API 凭据与 .env 约定」为准；括号内为早期命名，仍然兼容
BASE_URL_NAMES = ("BABELDOC_BASE_URL", "BABELDOC_WORKBENCH_BASE_URL")
API_KEY_NAMES = ("BABELDOC_API_KEY", "BABELDOC_WORKBENCH_API_KEY")
MODEL_NAMES = ("BABELDOC_MODEL", "BABELDOC_WORKBENCH_MODEL")
REASONING_NAMES = ("BABELDOC_REASONING", "BABELDOC_WORKBENCH_REASONING")
THINKING_NAMES = ("BABELDOC_THINKING", "BABELDOC_WORKBENCH_THINKING")
LANG_IN_NAMES = ("BABELDOC_LANG_IN",)
LANG_OUT_NAMES = ("BABELDOC_LANG_OUT",)
SMOKE_INPUT_NAMES = ("BABELDOC_SMOKE_INPUT",)
SMOKE_OUTPUT_NAMES = ("BABELDOC_SMOKE_OUTPUT",)


def parse_args(dotenv: dict[str, str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BabelDOC 工作台引擎冒烟测试")
    parser.add_argument(
        "--input",
        default=resolve(SMOKE_INPUT_NAMES, dotenv),
        help="待翻译 PDF 路径；缺省时读取 BABELDOC_SMOKE_INPUT 或自动生成样例",
    )
    parser.add_argument(
        "--output",
        default=resolve(SMOKE_OUTPUT_NAMES, dotenv) or ".tmp/smoke",
        help="输出目录",
    )
    parser.add_argument("--lang-in", default=resolve(LANG_IN_NAMES, dotenv) or "en")
    parser.add_argument("--lang-out", default=resolve(LANG_OUT_NAMES, dotenv) or "zh")
    parser.add_argument("--pages", default=None, help="页码范围，例如 1-2")
    parser.add_argument("--qps", type=int, default=4)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="跳过 LLM 翻译阶段（不需要 API Key，也不发起网络请求）",
    )
    parser.add_argument(
        "--cancel-after",
        type=float,
        default=None,
        help="启动多少秒后请求取消，用于验证取消语义",
    )
    parser.add_argument("--timeout", type=float, default=1800.0, help="总超时秒数")
    parser.add_argument(
        "--expect-error",
        action="store_true",
        help="负向测试：期望引擎返回结构化错误而不是成功",
    )
    return parser.parse_args()


def sample_font_path() -> str | None:
    """找一个可嵌入的 TrueType 字体；未嵌入字体的示例会被引擎判为扫描件。"""
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
    ]
    font_cache = Path.home() / ".cache" / "babeldoc" / "fonts"
    if font_cache.is_dir():
        candidates.extend(sorted(font_cache.glob("*.ttf")))
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def ensure_sample_pdf(output_dir: Path) -> Path:
    """生成一份含可提取英文文本的样例 PDF，便于离线自检。"""
    import pymupdf

    output_dir.mkdir(parents=True, exist_ok=True)
    sample_path = output_dir / "sample.en.pdf"
    if sample_path.exists():
        return sample_path
    font_path = sample_font_path()
    doc = pymupdf.open()
    for page_index in range(2):
        page = doc.new_page()
        font_kwargs = (
            {"fontname": "sample", "fontfile": font_path} if font_path else {}
        )
        page.insert_text(
            (72, 96),
            f"Smoke Test Page {page_index + 1}",
            fontsize=20,
            **font_kwargs,
        )
        page.insert_text(
            (72, 140),
            "BabelDOC workbench engine smoke test paragraph.\n"
            "This paragraph exists to verify layout parsing and typesetting.",
            fontsize=12,
            **font_kwargs,
        )
    doc.save(sample_path)
    doc.close()
    return sample_path


def build_api_config(offline: bool, dotenv: dict[str, str]) -> EngineApiConfig:
    if offline:
        return EngineApiConfig(model="offline-stub")
    return EngineApiConfig(
        model=resolve(MODEL_NAMES, dotenv) or "",
        base_url=resolve(BASE_URL_NAMES, dotenv) or None,
        api_key=resolve(API_KEY_NAMES, dotenv) or None,
        reasoning=resolve(REASONING_NAMES, dotenv) or None,
        thinking=resolve(THINKING_NAMES, dotenv) or None,
    )


def format_event(event: dict) -> str:
    event_type = event.get("type")
    payload = event.get("payload") or {}
    if event_type == "stage_summary":
        stages = "、".join(item.get("label", "") for item in payload.get("stages", []))
        return f"[阶段清单] 共 {len(payload.get('stages', []))} 个阶段：{stages}"
    if event_type in {"progress_start", "progress_update", "progress_end"}:
        return (
            f"[{payload.get('stage_label')}] {event_type} "
            f"阶段 {payload.get('stage_progress'):.1f}% "
            f"({payload.get('stage_current')}/{payload.get('stage_total')}) "
            f"总进度 {payload.get('overall_progress'):.1f}%"
        )
    if event_type == "finish":
        return f"[完成] 单语：{payload.get('mono_pdf_path')} 双语：{payload.get('dual_pdf_path')}"
    return f"[{event_type}] {payload}"


def main() -> int:
    dotenv = load_env_file(REPO_ROOT / DEFAULT_ENV_FILENAME)
    args = parse_args(dotenv)
    output_dir = (REPO_ROOT / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.input:
        input_path = Path(args.input)
        if not input_path.is_absolute():
            input_path = REPO_ROOT / input_path
    else:
        input_path = ensure_sample_pdf(output_dir)
    if not input_path.exists():
        print(f"❌ 找不到输入 PDF：{input_path}")
        return 2

    api = build_api_config(args.offline, dotenv)
    if not args.offline and not (api.model and api.api_key):
        print(
            "❌ 真实翻译需要 API 配置。请先设置环境变量：\n"
            "   Copy-Item env.example .env\n"
            "   然后填写 BABELDOC_BASE_URL / BABELDOC_MODEL / BABELDOC_API_KEY\n"
            "   或改用 --offline 只验证流程（不调用模型）。"
        )
        return 2

    request = EngineJobRequest(
        job_id=f"smoke-{int(time.time())}",
        input_path=str(input_path),
        output_dir=str(output_dir),
        lang_in=args.lang_in,
        lang_out=args.lang_out,
        pages=args.pages,
        qps=args.qps,
        skip_translation=args.offline,
        auto_extract_glossary=not args.offline,
    )

    print(f"输入：{input_path}")
    print(f"输出：{output_dir}")
    print(f"模式：{'离线自检（skip_translation）' if args.offline else '真实翻译'}")
    if not args.offline:
        # 只报告来源，绝不打印密钥内容
        print(
            "凭据来源："
            f"Base URL {describe_source(BASE_URL_NAMES, dotenv)}；"
            f"模型 {describe_source(MODEL_NAMES, dotenv)}；"
            f"Key {describe_source(API_KEY_NAMES, dotenv)}（不显示值）"
        )
    print("-" * 60)

    parent_conn, child_conn = CTX.Pipe(duplex=True)
    process = CTX.Process(
        target=worker_entry,
        args=(child_conn, request.to_dict(), api.to_dict()),
        name=f"babeldoc-worker-{request.job_id}",
    )
    process.start()
    child_conn.close()

    started = time.monotonic()
    cancel_sent = False
    finished_payload: dict | None = None
    cancelled = False
    error_payload: dict | None = None
    progress_values: list[float] = []
    exit_code: int | None = None
    seen_stage_summary = False

    while True:
        if (
            args.cancel_after is not None
            and not cancel_sent
            and time.monotonic() - started >= args.cancel_after
        ):
            print(f"→ 已发送取消请求（运行 {args.cancel_after:.1f} 秒）")
            parent_conn.send({"type": MSG_CONTROL, "action": CONTROL_CANCEL})
            cancel_sent = True

        if parent_conn.poll(0.2):
            try:
                message = parent_conn.recv()
            except EOFError:
                break
            if not isinstance(message, dict):
                continue
            if message.get("type") == MSG_EVENT:
                event = message.get("event") or {}
                print(format_event(event))
                event_type = event.get("type")
                payload = event.get("payload") or {}
                if event_type == "stage_summary":
                    seen_stage_summary = True
                elif event_type in {"progress_start", "progress_update", "progress_end"}:
                    value = payload.get("overall_progress")
                    if isinstance(value, (int, float)):
                        progress_values.append(float(value))
                elif event_type == "finish":
                    finished_payload = payload
                elif event_type == "cancelled":
                    cancelled = True
                elif event_type == "error":
                    error_payload = payload
            elif message.get("type") == MSG_EXIT:
                exit_code = message.get("code")
                break
        elif not process.is_alive() and not parent_conn.poll():
            break

        if time.monotonic() - started > args.timeout:
            print(f"❌ 超过总超时 {args.timeout:.0f} 秒，终止工作进程")
            process.terminate()
            process.join(10)
            return 1

    process.join(30)
    if process.is_alive():
        print("⚠️ 工作进程未在 30 秒内退出，强制终止")
        process.terminate()
        process.join(10)
    elapsed = time.monotonic() - started

    print("-" * 60)
    ok = True

    if cancel_sent:
        if not cancelled:
            print("❌ 取消后没有收到 cancelled 事件")
            ok = False
        elif finished_payload is not None:
            print("❌ 取消的任务不应产生 finish 事件")
            ok = False
        else:
            print("✅ 取消语义正确：只产生 cancelled 事件，没有成功标记")
    else:
        if not seen_stage_summary:
            print("❌ 没有收到 stage_summary 事件")
            ok = False
        if args.expect_error:
            if error_payload is None:
                print("❌ 期望结构化错误，但没有收到 error 事件")
                ok = False
            elif finished_payload is not None:
                print("❌ 期望结构化错误，但收到了 finish 事件")
                ok = False
            else:
                print(
                    "✅ 结构化错误正确："
                    f"code={error_payload.get('code')} "
                    f"type={error_payload.get('error_type')} "
                    f"hint={error_payload.get('hint')}"
                )
        elif error_payload is not None:
            print(f"❌ 引擎返回错误：{error_payload}")
            ok = False
        if finished_payload is None and not args.expect_error:
            print(f"❌ 没有收到 finish 事件（进程退出码 {exit_code}）")
            ok = False
        if progress_values and progress_values != sorted(progress_values):
            print("❌ overall_progress 出现回退")
            ok = False
        if progress_values and max(progress_values) < 100:
            print(f"⚠️ overall_progress 未到达 100（最大 {max(progress_values):.1f}）")

    if finished_payload and ok:
        for key in ("mono_pdf_path", "dual_pdf_path", "auto_extracted_glossary_path"):
            path_value = finished_payload.get(key)
            if path_value and not Path(path_value).exists():
                print(f"❌ 成果文件不存在：{key} = {path_value}")
                ok = False
        if ok:
            print("✅ 完成事件与成果文件一致")

    print(f"耗时：{elapsed:.1f} 秒，工作进程退出码：{exit_code}")
    if ok and args.expect_error:
        print("✅ 负向测试通过")
        return 0
    if ok and not cancel_sent and finished_payload:
        print("✅ 冒烟测试通过")
        return 0
    if ok and cancel_sent:
        print("✅ 取消路径验证通过")
        return 0
    print("❌ 冒烟测试未通过")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
