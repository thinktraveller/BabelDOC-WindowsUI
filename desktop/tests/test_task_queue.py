"""任务队列、状态机与取消的离线测试（工作进程用替身）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from babeldoc_workbench.models import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_SUCCEEDED,
)
from babeldoc_workbench.services import api_profiles, task_store
from babeldoc_workbench.services.task_queue import QueueConfig, TaskQueue, ensure_task_dirs
from engine_fakes import ConcurrencyTracker, make_fake_runner

FAKE_KEY = "sk-fake-queue-key"


def build_queue(runner, **config_overrides) -> TaskQueue:
    config = QueueConfig(
        poll_interval=0.05, db_event_interval=0.05, sse_event_interval=0.0, **config_overrides
    )
    return TaskQueue(runner=runner, config=config)


def make_task(*, profile_id: int | None, params: dict | None = None) -> "object":
    task = task_store.create_task(
        input_name="sample.pdf",
        stored_input_path=Path("."),
        output_dir=Path("."),
        work_dir=Path("."),
        log_dir=Path("."),
        api_profile_id=profile_id,
        params_snapshot=params or {"lang_in": "en", "lang_out": "zh"},
    )
    dirs = ensure_task_dirs(task.id)
    input_path = dirs["input"] / "sample.pdf"
    input_path.write_bytes(b"%PDF-1.4 fake input")
    task_store.finalize_new_task(task, input_path=input_path, dirs=dirs)
    return task


def wait_for_status(task_id: int, statuses: set[str], timeout: float = 20.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = task_store.get_task(task_id).status
        if status in statuses:
            return status
        time.sleep(0.05)
    return task_store.get_task(task_id).status


@pytest.fixture
def profile(workbench_db, fake_keyring):
    return api_profiles.create_profile(
        name="队列测试",
        base_url="https://example.test/v1",
        model="demo",
        api_key=FAKE_KEY,
    )


def test_three_tasks_run_in_order_one_at_a_time(workbench_db, profile) -> None:
    tracker = ConcurrencyTracker()
    queue = build_queue(make_fake_runner(tracker=tracker))
    queue.start()
    try:
        tasks = [make_task(profile_id=profile["id"]) for _ in range(3)]
        for task in tasks:
            queue.submit(task.id)
        statuses = [wait_for_status(task.id, {TASK_STATUS_SUCCEEDED}) for task in tasks]
    finally:
        queue.stop()

    assert statuses == [TASK_STATUS_SUCCEEDED] * 3
    assert tracker.max_seen == 1, "同一时刻只能运行一个任务"
    assert tracker.order == [f"task-{task.id}" for task in tasks], "必须按提交顺序执行"
    for task in tasks:
        refreshed = task_store.get_task(task.id)
        assert refreshed.progress == 100.0
        assert refreshed.finished_at is not None


def test_outputs_are_indexed_and_removal_is_detected(workbench_db, profile) -> None:
    queue = build_queue(make_fake_runner())
    queue.start()
    try:
        task = make_task(profile_id=profile["id"])
        queue.submit(task.id)
        assert wait_for_status(task.id, {TASK_STATUS_SUCCEEDED}) == TASK_STATUS_SUCCEEDED
    finally:
        queue.stop()

    outputs = task_store.outputs_of(task_store.get_task(task.id))
    kinds = {item["kind"]: item for item in outputs}
    assert set(kinds) >= {"mono", "dual", "glossary", "log"}
    assert kinds["mono"]["exists"] is True

    Path(kinds["mono"]["path"]).unlink()
    refreshed = {
        item["kind"]: item for item in task_store.outputs_of(task_store.get_task(task.id))
    }
    assert refreshed["mono"]["exists"] is False
    assert refreshed["dual"]["exists"] is True


def test_failure_records_code_and_message(workbench_db, profile) -> None:
    runner = make_fake_runner(
        fail={
            "code": "engine_failure",
            "error_type": "AuthenticationError",
            "message": "认证失败",
            "hint": "检查 API Key",
        },
        write_outputs=False,
        finish=False,
    )
    queue = build_queue(runner)
    queue.start()
    try:
        task = make_task(profile_id=profile["id"])
        queue.submit(task.id)
        assert wait_for_status(task.id, {TASK_STATUS_FAILED}) == TASK_STATUS_FAILED
    finally:
        queue.stop()

    failed = task_store.get_task(task.id)
    assert failed.error_code == "engine_failure"
    assert "认证失败" in (failed.error_message or "")
    assert "检查 API Key" in (failed.error_message or "")
    assert result_outputs(failed) == []


def test_running_task_can_be_cancelled(workbench_db, profile) -> None:
    queue = build_queue(make_fake_runner(step_delay=0.3, wait_for_cancel=True))
    queue.start()
    try:
        task = make_task(profile_id=profile["id"])
        queue.submit(task.id)
        deadline = time.monotonic() + 10
        while (
            task_store.get_task(task.id).status != "running"
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        result = queue.cancel(task.id)
        assert result["status"] == "cancelling"
        assert wait_for_status(task.id, {TASK_STATUS_CANCELLED}) == TASK_STATUS_CANCELLED
    finally:
        queue.stop()

    cancelled = task_store.get_task(task.id)
    assert cancelled.status == TASK_STATUS_CANCELLED
    assert cancelled.cancel_requested is True
    assert result_outputs(cancelled) == [], "取消后不得登记任何成果"
    events = task_store.recent_events(cancelled.id)
    assert events[-1]["type"] == "cancelled"


def test_queued_task_can_be_cancelled_before_running(workbench_db, profile) -> None:
    queue = build_queue(make_fake_runner(step_delay=0.2, wait_for_cancel=True))
    queue.start()
    try:
        first = make_task(profile_id=profile["id"])
        second = make_task(profile_id=profile["id"])
        queue.submit(first.id)
        queue.submit(second.id)
        deadline = time.monotonic() + 10
        while task_store.get_task(first.id).status != "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert task_store.get_task(second.id).status == TASK_STATUS_QUEUED
        result = queue.cancel(second.id)
        assert result["status"] == TASK_STATUS_CANCELLED
        assert task_store.get_task(second.id).status == TASK_STATUS_CANCELLED
        first_summary = queue.cancel(first.id)
        assert first_summary["status"] == "cancelling"
        wait_for_status(first.id, {TASK_STATUS_CANCELLED})
    finally:
        queue.stop()


def test_missing_api_key_fails_without_running_engine(workbench_db, profile) -> None:
    calls: list[str] = []

    def runner(**kwargs):
        calls.append("called")
        raise AssertionError("不应启动工作进程")

    queue = build_queue(runner)
    queue.start()
    try:
        task = make_task(profile_id=profile["id"])
        # 该配置有 Key，改为构造一个没有 Key 的配置
        without_key = api_profiles.create_profile(
            name="无 Key", base_url="https://example.test/v1", model="demo"
        )
        task.api_profile_id = without_key["id"]
        task.save()
        queue.submit(task.id)
        assert wait_for_status(task.id, {TASK_STATUS_FAILED}) == TASK_STATUS_FAILED
    finally:
        queue.stop()

    failed = task_store.get_task(task.id)
    assert failed.error_code == "missing_api_key"
    assert calls == []


def test_rerun_creates_new_task_and_keeps_history(workbench_db, profile) -> None:
    runner = make_fake_runner(
        fail={"code": "engine_failure", "message": "第一次失败"},
        write_outputs=False,
        finish=False,
    )
    queue = build_queue(runner)
    queue.start()
    try:
        task = make_task(profile_id=profile["id"], params={"lang_out": "zh", "qps": 7})
        queue.submit(task.id)
        assert wait_for_status(task.id, {TASK_STATUS_FAILED}) == TASK_STATUS_FAILED
    finally:
        queue.stop()

    original = task_store.get_task(task.id)
    new_task = task_store.create_task(
        input_name=original.input_name,
        stored_input_path=Path(original.stored_input_path),
        output_dir=Path("."),
        work_dir=Path("."),
        log_dir=Path("."),
        api_profile_id=original.api_profile_id,
        params_snapshot=task_store.params_snapshot(original),
    )
    assert new_task.id != original.id
    assert task_store.params_snapshot(new_task)["qps"] == 7
    assert task_store.get_task(original.id).status == TASK_STATUS_FAILED
    assert task_store.get_task(original.id).error_message == "第一次失败"


def test_event_retention_keeps_recent_only(workbench_db, profile) -> None:
    task = make_task(profile_id=profile["id"])
    for index in range(task_store.EVENT_RETENTION + 60):
        task_store.append_event(task, "progress_update", {"overall_progress": index})
    events = task_store.recent_events(task.id, limit=10_000)
    assert len(events) <= task_store.EVENT_RETENTION + 25
    assert events[-1]["payload"]["overall_progress"] >= task_store.EVENT_RETENTION


def result_outputs(task) -> list[dict]:
    """只关心成果文件；任务事件日志（kind=log）不算成果。"""
    return [
        item
        for item in task_store.outputs_of(task)
        if item["kind"] in {"mono", "dual", "glossary"}
    ]
