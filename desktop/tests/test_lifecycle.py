"""成果操作与文件生命周期（分层删除）的离线测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from babeldoc_workbench.services import lifecycle, task_store
from babeldoc_workbench.services.task_queue import ensure_task_dirs


def make_task_with_files(task_id_label: str = "样例") -> "object":
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
    input_path.write_bytes(b"input-bytes")
    (dirs["work"] / "temp.bin").write_bytes(b"work-bytes" * 10)
    (dirs["output"] / "sample.zh.mono.pdf").write_bytes(b"mono-bytes")
    (dirs["output"] / "sample.zh.dual.pdf").write_bytes(b"dual")
    (dirs["logs"] / task_store.EVENT_LOG_NAME).write_text("{}", encoding="utf-8")
    task_store.finalize_new_task(task, input_path=input_path, dirs=dirs)
    task_store.refresh_outputs(task)
    return task


def test_task_root_rejects_escape_attempt(workbench_db) -> None:
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.task_root("../etc")


def test_describe_task_files_reports_sizes(workbench_db) -> None:
    task = make_task_with_files()
    described = lifecycle.describe_task_files(task.id)
    by_name = {item["name"]: item for item in described["items"]}
    assert by_name["input"]["size"] == len(b"input-bytes")
    assert by_name["output"]["size"] == len(b"mono-bytes") + len(b"dual")
    assert described["total_size"] > 0
    assert described["root"].startswith(str(lifecycle.tasks_root()))


def test_clear_temp_files_keeps_input_and_outputs(workbench_db) -> None:
    task = make_task_with_files()
    result = lifecycle.clear_temp_files(task.id)
    root = lifecycle.task_root(task.id)
    assert result["freed_bytes"] > 0
    assert not (root / "work").exists()
    assert (root / "input" / "sample.pdf").is_file()
    assert (root / "output" / "sample.zh.mono.pdf").is_file()
    outputs = {item["kind"] for item in task_store.outputs_of(task_store.get_task(task.id))}
    assert {"mono", "dual"} <= outputs


def test_delete_managed_files_keeps_record_and_marks_outputs_missing(workbench_db) -> None:
    task = make_task_with_files()
    result = lifecycle.delete_managed_files(task.id)
    root = lifecycle.task_root(task.id)
    assert result["freed_bytes"] > 0
    for name in ("input", "work", "output", "logs"):
        assert not (root / name).exists()
    # 记录仍在，只是成果索引标记为不存在
    refreshed = task_store.get_task(task.id)
    assert refreshed.input_name == "sample.pdf"
    assert all(item["exists"] is False for item in task_store.outputs_of(refreshed))


def test_output_path_reports_missing_file(workbench_db) -> None:
    task = make_task_with_files()
    path = lifecycle.output_path(task_store.get_task(task.id), "mono")
    assert path.is_file()
    path.unlink()
    with pytest.raises(lifecycle.LifecycleError) as excinfo:
        lifecycle.output_path(task_store.get_task(task.id), "mono")
    assert "不存在或被移动" in str(excinfo.value)


def test_output_path_rejects_unknown_kind(workbench_db) -> None:
    task = make_task_with_files()
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.output_path(task_store.get_task(task.id), "log")


def test_save_copy_copies_without_removing_source(workbench_db, tmp_path: Path) -> None:
    task = make_task_with_files()
    target = tmp_path / "另存目录"
    target.mkdir()
    result = lifecycle.save_copy(task_store.get_task(task.id), "mono", target)
    saved = Path(result["target"])
    assert saved.is_file() and saved.parent == target
    assert result["size"] == len(b"mono-bytes")
    source = lifecycle.output_path(task_store.get_task(task.id), "mono")
    assert source.is_file(), "另存不得删除原成果"


def test_save_copy_rejects_existing_file(workbench_db, tmp_path: Path) -> None:
    task = make_task_with_files()
    target = tmp_path / "target"
    target.mkdir()
    lifecycle.save_copy(task_store.get_task(task.id), "mono", target)
    with pytest.raises(lifecycle.LifecycleError) as excinfo:
        lifecycle.save_copy(task_store.get_task(task.id), "mono", target)
    assert "同名文件" in str(excinfo.value)


def test_save_copy_rejects_missing_directory(workbench_db, tmp_path: Path) -> None:
    task = make_task_with_files()
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.save_copy(task_store.get_task(task.id), "dual", tmp_path / "不存在")
