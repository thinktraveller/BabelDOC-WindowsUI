"""步骤 5 的真实引擎端到端验证：走队列 + 真实工作进程。

与 `test_task_queue.py` 里的测试替身不同，本脚本使用真实引擎与真实 spawn 工作进程，
但跳过 LLM 翻译（`skip_translation`，需要 BABELDOC_ALLOW_SKIP_TRANSLATION=1），
因此不需要 API 凭据。验证结果与替身测试分别记录。

用法::

    .\\.venv\\Scripts\\python.exe desktop\\tests\\verify_queue_real_engine.py --input <英文 PDF>
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "desktop" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from babeldoc_workbench import db  # noqa: E402
from babeldoc_workbench.services import task_store  # noqa: E402
from babeldoc_workbench.services.files import stash  # noqa: E402
from babeldoc_workbench.services.task_queue import (  # noqa: E402
    QueueConfig,
    TaskQueue,
    ensure_task_dirs,
)
from babeldoc_workbench.settings import ensure_app_dirs, set_current_paths  # noqa: E402

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="队列 + 真实引擎端到端验证")
    parser.add_argument("--input", required=True, help="待翻译 PDF 路径")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--pages", default="1-2", help="页码范围，空字符串表示全部")
    parser.add_argument(
        "--cancel-after",
        type=float,
        default=None,
        help="运行多少秒后请求取消（验证真实引擎的取消路径）",
    )
    parser.add_argument(
        "--lifecycle-check",
        action="store_true",
        help="成功后检查成果操作与分层删除（另存、清理临时文件、删除应用管理的文件）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"❌ 找不到输入文件：{input_path}")
        return 2

    os.environ["BABELDOC_ALLOW_SKIP_TRANSLATION"] = "1"
    app_dir = Path(tempfile.mkdtemp(prefix="babeldoc-verify-queue-"))
    paths = ensure_app_dirs(app_dir)
    set_current_paths(paths)
    db.init(paths.db)

    queue = TaskQueue(config=QueueConfig(poll_interval=0.2))
    queue.start()
    try:
        staged = stash(input_path, input_path.name)
        params: dict[str, object] = {
            "lang_in": "en",
            "lang_out": "zh",
            "qps": 4,
            "auto_extract_glossary": False,
        }
        if args.pages:
            params["pages"] = args.pages
        params["skip_translation"] = True
        task = task_store.create_task(
            input_name=staged["name"],
            stored_input_path=Path(staged["path"]),
            output_dir=Path("."),
            work_dir=Path("."),
            log_dir=Path("."),
            api_profile_id=None,
            params_snapshot=params,
        )
        dirs = ensure_task_dirs(task.id)
        target = dirs["input"] / staged["name"]
        shutil.copyfile(staged["path"], target)
        task_store.finalize_new_task(task, input_path=target, dirs=dirs)
        queue.submit(task.id)

        started = time.monotonic()
        cancel_requested = False
        status = "queued"
        while time.monotonic() - started < args.timeout:
            if (
                args.cancel_after is not None
                and not cancel_requested
                and time.monotonic() - started >= args.cancel_after
            ):
                print(f"→ 已请求取消（运行 {args.cancel_after:.1f} 秒）", flush=True)
                queue.cancel(task.id)
                cancel_requested = True
            status = task_store.get_task(task.id).status
            if status in TERMINAL:
                break
            time.sleep(0.2)
        elapsed = time.monotonic() - started
    finally:
        queue.stop()

    refreshed = task_store.get_task(task.id)
    outputs = task_store.outputs_of(refreshed)
    result_outputs = [
        item for item in outputs if item["kind"] in {"mono", "dual", "glossary"}
    ]
    print("步骤 5 队列 + 真实引擎端到端验证")
    print("-" * 60)
    print(f"任务 ID：{refreshed.id}")
    print(f"最终状态：{refreshed.status}（耗时 {elapsed:.1f} 秒）")
    print(f"最后阶段：{refreshed.stage}（进度 {refreshed.progress}）")
    print(f"事件条数：{len(task_store.recent_events(refreshed.id, limit=1000))}")
    valid = []
    for item in result_outputs:
        path = Path(item["path"])
        ok = path.is_file() and path.stat().st_size > 0
        valid.append(ok)
        print(f"{'✅' if ok else '❌'} {item['kind']}：{path}（{item['size']} 字节）")
    ok = refreshed.status == "succeeded" and valid and all(valid)
    if args.cancel_after is None and refreshed.status != "succeeded":
        print(f"❌ 任务未成功：{refreshed.error_code} / {refreshed.error_message}")
    if args.cancel_after is not None:
        # 取消模式：必须停在 cancelled，且不得登记任何成果
        ok = refreshed.status == "cancelled" and not result_outputs
        print(
            f"{'✅' if refreshed.status == 'cancelled' else '❌'} 取消后状态：{refreshed.status}"
        )
        print(f"{'✅' if not result_outputs else '❌'} 取消后成果数量：{len(result_outputs)}")
    print("-" * 60)
    print(f"总体结论：{'✅ ok' if ok else '❌ fail'}")

    if ok and args.lifecycle_check:
        from babeldoc_workbench.services import lifecycle

        print("--- 成果操作与文件生命周期 ---")
        target_dir = Path(tempfile.mkdtemp(prefix="babeldoc-verify-saveas-"))
        saved = lifecycle.save_copy(refreshed, "mono", target_dir)
        saved_ok = Path(saved["target"]).is_file()
        source_ok = Path(saved["source"]).is_file()
        print(f"{'✅' if saved_ok else '❌'} 另存单语成果 → {saved['target']}")
        print(f"{'✅' if source_ok else '❌'} 另存后原成果仍存在")

        before = lifecycle.describe_task_files(refreshed.id)
        cleanup = lifecycle.clear_temp_files(refreshed.id)
        after = lifecycle.describe_task_files(refreshed.id)
        after_by_name = {item["name"]: item for item in after["items"]}
        temp_ok = not after_by_name["work"]["exists"]
        keep_ok = after_by_name["input"]["exists"] and after_by_name["output"]["exists"]
        print(
            f"{'✅' if temp_ok else '❌'} 清理临时文件释放 {cleanup['freed_bytes']} 字节，"
            "work 目录已删除"
        )
        print(f"{'✅' if keep_ok else '❌'} 清理后输入副本与成果仍在")

        removed = lifecycle.delete_managed_files(refreshed.id)
        root = lifecycle.task_root(refreshed.id)
        managed_gone = all(not (root / name).exists() for name in ("input", "work", "output", "logs"))
        print(
            f"{'✅' if managed_gone else '❌'} 删除应用管理的文件（释放 "
            f"{removed['freed_bytes']} 字节），输入副本与成果已清除"
        )
        print(f"   清理前任务目录占用：{before['total_size']} 字节")
        ok = ok and saved_ok and source_ok and temp_ok and keep_ok and managed_gone
        print(f"总体结论（含生命周期检查）：{'✅ ok' if ok else '❌ fail'}")
    db.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
