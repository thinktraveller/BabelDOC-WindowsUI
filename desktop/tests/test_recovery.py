"""重启恢复：遗留任务标记与残留工作进程清理。"""

from __future__ import annotations

from pathlib import Path

import pytest

from babeldoc_workbench.models import TASK_STATUS_INTERRUPTED
from babeldoc_workbench.services import recovery, task_store
from babeldoc_workbench.services.task_queue import ensure_task_dirs


def make_task(status: str) -> "object":
    task = task_store.create_task(
        input_name="sample.pdf",
        stored_input_path=Path("."),
        output_dir=Path("."),
        work_dir=Path("."),
        log_dir=Path("."),
        api_profile_id=None,
        params_snapshot={"lang_out": "zh"},
    )
    dirs = ensure_task_dirs(task.id)
    input_path = dirs["input"] / "sample.pdf"
    input_path.write_bytes(b"%PDF-1.4")
    (dirs["output"] / "sample.zh.mono.pdf").write_bytes(b"partial-output")
    task_store.finalize_new_task(task, input_path=input_path, dirs=dirs)
    task.stage = "Translate Paragraphs"
    task.status = status
    task.save()
    return task


def test_running_task_becomes_interrupted_on_startup(workbench_db) -> None:
    task = make_task("running")
    result = recovery.recover_on_startup()
    assert result["interrupted_tasks"][0]["task_id"] == task.id
    refreshed = task_store.get_task(task.id)
    assert refreshed.status == TASK_STATUS_INTERRUPTED
    assert refreshed.error_code == "interrupted"
    assert "可重新执行" in (refreshed.error_message or "")
    assert refreshed.finished_at is not None
    events = task_store.recent_events(task.id)
    assert events[-1]["payload"]["status"] == "interrupted"
    assert events[-1]["payload"]["note"] == "可能不完整"


def test_preparing_task_is_also_marked(workbench_db) -> None:
    task = make_task("preparing")
    recovery.mark_interrupted_tasks()
    assert task_store.get_task(task.id).status == TASK_STATUS_INTERRUPTED


def test_interrupted_outputs_are_kept_but_flagged(workbench_db) -> None:
    task = make_task("running")
    recovery.mark_interrupted_tasks()
    refreshed = task_store.get_task(task.id)
    outputs = task_store.outputs_of(refreshed)
    assert outputs and outputs[0]["exists"] is True, "中断任务已有的成果文件应保留"


def test_terminal_tasks_are_not_touched(workbench_db) -> None:
    task = make_task("succeeded")
    recovery.mark_interrupted_tasks()
    assert task_store.get_task(task.id).status == "succeeded"


class FakeProcess:
    def __init__(self, pid: int, ppid: int, name: str, cmdline: list[str], exe: str):
        self.info = {"pid": pid, "ppid": ppid, "name": name, "cmdline": cmdline}
        self._exe = exe
        self._name = name

    def exe(self) -> str:
        return self._exe

    def name(self) -> str:
        return self._name


def test_find_orphan_workers_detects_dead_parent(monkeypatch) -> None:
    processes = [
        FakeProcess(101, 999, "python.exe", ["python", "--multiprocessing-fork"], "C:/py.exe"),
        FakeProcess(102, 1, "python.exe", ["python", "--multiprocessing-fork"], "C:/py.exe"),
        FakeProcess(103, 1, "python.exe", ["python", "-m", "pytest"], "C:/py.exe"),
    ]
    parents = {1: True, 999: False}

    def parent_alive(pid: int) -> bool:
        return parents.get(pid, False)

    found = recovery.find_orphan_workers(
        process_iter=lambda attrs=None: processes,
        parent_alive=parent_alive,
        self_exe="C:/py.exe",
        self_name="python.exe",
    )
    assert [item["pid"] for item in found] == [101]


def test_find_orphan_workers_ignores_other_executables(workbench_db) -> None:
    processes = [
        FakeProcess(201, 1, "python.exe", ["x", "--multiprocessing-fork"], "C:/other.exe"),
    ]
    found = recovery.find_orphan_workers(
        process_iter=lambda attrs=None: processes,
        parent_alive=lambda pid: False,
        self_exe="C:/py.exe",
        self_name="python.exe",
    )
    assert found == []


def test_worker_registry_roundtrip(workbench_db) -> None:
    from babeldoc_workbench.services import worker_registry

    worker_registry.register(4321)
    worker_registry.register(4322)
    assert sorted(worker_registry.list_pids()) == [4321, 4322]
    worker_registry.unregister(4321)
    assert worker_registry.list_pids() == [4322]
    worker_registry.clear()
    assert worker_registry.list_pids() == []


def test_registered_workers_are_terminated_on_startup(workbench_db, monkeypatch) -> None:
    from babeldoc_workbench.services import recovery as recovery_module
    from babeldoc_workbench.services import worker_registry

    terminated: list[int] = []

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

        def terminate(self) -> None:
            terminated.append(self.pid)

        def wait(self, timeout: float = 0) -> None:
            return None

    import psutil

    monkeypatch.setattr(psutil, "Process", FakeProc)
    worker_registry.register(7777)
    killed = recovery_module.terminate_registered_workers()
    assert killed == [7777]
    assert terminated == [7777]
    assert worker_registry.list_pids() == []


def test_force_interrupt_marks_active_tasks(workbench_db) -> None:
    task = make_task("running")
    interrupted = recovery.force_interrupt_active_tasks()
    assert [item["task_id"] for item in interrupted] == [task.id]
    assert task_store.get_task(task.id).status == TASK_STATUS_INTERRUPTED
