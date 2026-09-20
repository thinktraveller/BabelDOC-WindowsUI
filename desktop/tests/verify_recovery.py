"""步骤 9 的真实崩溃恢复验证。

流程：在子进程里启动服务并提交一个真实任务（跳过 LLM）→ 任务进入 running 后强杀该进程
（模拟断电/任务管理器结束进程）→ 在父进程里执行启动恢复 → 校验任务被标记为 interrupted、
残留工作进程被清理、任务不会被自动重跑、历史与成果索引仍在。

用法::

    .\\.venv\\Scripts\\python.exe desktop\\tests\\verify_recovery.py --input <英文 PDF>
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "desktop" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

CHILD_SCRIPT = r"""
import json, os, shutil, sys, time
from pathlib import Path

from babeldoc_workbench import db
from babeldoc_workbench.services import task_store
from babeldoc_workbench.services.task_queue import QueueConfig, TaskQueue, ensure_task_dirs
from babeldoc_workbench.settings import ensure_app_dirs, set_current_paths

input_path = Path(sys.argv[1])
paths = ensure_app_dirs()
set_current_paths(paths)
db.init(paths.db)
queue = TaskQueue(config=QueueConfig(poll_interval=0.2))
queue.start()
task = task_store.create_task(
    input_name=input_path.name,
    stored_input_path=Path("."),
    output_dir=Path("."),
    work_dir=Path("."),
    log_dir=Path("."),
    api_profile_id=None,
    params_snapshot={
        "lang_in": "en",
        "lang_out": "zh",
        "pages": "1-2",
        "skip_translation": True,
    },
)
dirs = ensure_task_dirs(task.id)
target = dirs["input"] / input_path.name
shutil.copyfile(input_path, target)
task_store.finalize_new_task(task, input_path=target, dirs=dirs)
queue.submit(task.id)
print(f"READY {task.id}", flush=True)
while True:
    time.sleep(1)
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="崩溃恢复验证")
    parser.add_argument("--input", required=True, help="用于触发真实任务的 PDF")
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


def read_status(db_path: Path, task_id: int) -> str:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return row[0] if row else "missing"
    finally:
        connection.close()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).resolve()
    if not input_path.is_file():
        print(f"❌ 找不到输入文件：{input_path}")
        return 2

    app_dir = Path(tempfile.mkdtemp(prefix="babeldoc-verify-recovery-"))
    env = dict(os.environ)
    env["BABELDOC_APP_DIR"] = str(app_dir)
    env["BABELDOC_ALLOW_SKIP_TRANSLATION"] = "1"
    env["PYTHONPATH"] = str(BACKEND_DIR)
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", CHILD_SCRIPT, str(input_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
    )

    db_path = app_dir / "db" / "workbench.db"
    task_id: int | None = None
    started = time.monotonic()
    try:
        assert child.stdout is not None
        deadline = started + 60
        while time.monotonic() < deadline:
            line = child.stdout.readline()
            if line.startswith("READY"):
                task_id = int(line.split()[1])
                break
        if task_id is None:
            print("❌ 子进程没有启动任务")
            return 1

        print(f"子任务已创建：#{task_id}")
        status = "queued"
        while time.monotonic() < started + args.timeout:
            status = read_status(db_path, task_id)
            if status == "running":
                break
            if status in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.3)
        print(f"强杀前状态：{status}")
        if status != "running":
            print("❌ 任务没有进入 running，无法模拟崩溃")
            return 1
    finally:
        child.kill()
        child.wait(timeout=20)
    print("已强制结束服务进程（模拟断电/任务管理器结束进程）")

    print("--- 重启并执行启动恢复 ---")
    import importlib

    from babeldoc_workbench import db as db_module

    importlib.reload(db_module)
    from babeldoc_workbench.services import recovery
    from babeldoc_workbench.settings import ensure_app_dirs, set_current_paths

    paths = ensure_app_dirs(app_dir)
    set_current_paths(paths)
    db_module.init(paths.db)
    recovered = recovery.recover_on_startup()

    from babeldoc_workbench.services import task_store

    task = task_store.get_task(task_id)
    outputs = task_store.outputs_of(task)
    events = task_store.recent_events(task_id)

    time.sleep(3)
    still = task_store.get_task(task_id).status

    checks = [
        ("任务被标记为 interrupted", task.status == "interrupted", task.status),
        ("记录了中断原因", task.error_code == "interrupted", task.error_code or ""),
        (
            "残留工作进程被清理",
            bool(recovered["orphan_workers"]),
            f"清理 {len(recovered['orphan_workers'])} 个",
        ),
        (
            "不会自动重跑",
            still == "interrupted",
            f"3 秒后仍为 {still}",
        ),
        (
            "历史事件保留",
            any(item["type"] == "status" for item in events),
            f"{len(events)} 条事件",
        ),
        (
            "成果索引仍可查询",
            isinstance(outputs, list),
            f"{len(outputs)} 项",
        ),
    ]
    print("-" * 60)
    for label, passed, detail in checks:
        print(f"{'✅' if passed else '❌'} {label}：{detail}")
    print("-" * 60)
    failed = [label for label, passed, _ in checks if not passed]
    print(f"总体结论：{'✅ ok' if not failed else '❌ 失败项：' + '、'.join(failed)}")
    db_module.close()
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
