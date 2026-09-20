"""术语资料库：导入、审核、冲突与版本的离线测试。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from babeldoc_workbench.models import GLOSSARY_STATUS_CONFLICT
from babeldoc_workbench.services import glossaries, task_store
from babeldoc_workbench.services.task_queue import QueueConfig, TaskQueue, ensure_task_dirs
from engine_fakes import make_fake_runner

CSV_BASIC = "source,target,tgt_lng\n神经网络,neural network,zh\n模型,model,zh\n"
CSV_CONFLICT = "source,target,tgt_lng\n模型,模型对象,zh\n"
CSV_WITH_BOM = "\ufeffsource,target,tgt_lng\n注意力机制,attention,zh\n"


def import_text(text: str, *, name: str = "测试术语", tgt_lng: str = "zh") -> dict:
    return glossaries.import_from_file(
        io.BytesIO(text.encode("utf-8")), name=name, tgt_lng=tgt_lng
    )


def test_import_csv_creates_entries(workbench_db) -> None:
    result = import_text(CSV_BASIC)
    assert result["entry_count"] == 2
    assert result["summary"]["added"] == 2
    entries = glossaries.list_entries(result["id"])["items"]
    assert {entry["source"] for entry in entries} == {"神经网络", "模型"}
    assert all(entry["status"] == "new" for entry in entries)
    assert all(entry["tgt_lng"] == "zh" for entry in entries)


def test_import_requires_source_and_target_columns(workbench_db) -> None:
    with pytest.raises(glossaries.GlossaryError) as excinfo:
        import_text("term,translation\n模型,model\n")
    assert "source" in str(excinfo.value)


def test_import_rejects_empty_content(workbench_db) -> None:
    with pytest.raises(glossaries.GlossaryError):
        import_text("source,target\n,\n")


def test_import_handles_utf8_bom(workbench_db) -> None:
    result = import_text(CSV_WITH_BOM)
    assert result["entry_count"] == 1


def test_import_skips_rows_with_other_language(workbench_db) -> None:
    csv_text = "source,target,tgt_lng\nモデル,model,ja\n模型,model,zh\n"
    result = import_text(csv_text)
    assert result["entry_count"] == 1


def test_reimport_marks_conflict_without_overwriting(workbench_db) -> None:
    glossary = import_text(CSV_BASIC)
    summary = glossaries.import_rows(
        glossaries.get_glossary(glossary["id"]), glossaries.parse_csv(CSV_CONFLICT.encode())
    )
    assert summary["conflicts"] == 1
    entries = {
        entry["source"]: entry for entry in glossaries.list_entries(glossary["id"])["items"]
    }
    conflicted = entries["模型"]
    assert conflicted["status"] == GLOSSARY_STATUS_CONFLICT
    assert conflicted["target"] == "model", "原有译词不得被静默覆盖"
    assert conflicted["conflict_target"] == "模型对象"


def test_resolve_conflict_keeps_existing(workbench_db) -> None:
    glossary = import_text(CSV_BASIC)
    glossaries.import_rows(
        glossaries.get_glossary(glossary["id"]), glossaries.parse_csv(CSV_CONFLICT.encode())
    )
    entry = next(
        item
        for item in glossaries.list_entries(glossary["id"])["items"]
        if item["source"] == "模型"
    )
    resolved = glossaries.resolve_conflict(entry["id"], keep="existing")
    assert resolved["target"] == "model"
    assert resolved["conflict_target"] is None
    assert resolved["status"] == "approved"


def test_resolve_conflict_uses_incoming(workbench_db) -> None:
    glossary = import_text(CSV_BASIC)
    glossaries.import_rows(
        glossaries.get_glossary(glossary["id"]), glossaries.parse_csv(CSV_CONFLICT.encode())
    )
    entry = next(
        item
        for item in glossaries.list_entries(glossary["id"])["items"]
        if item["source"] == "模型"
    )
    resolved = glossaries.resolve_conflict(entry["id"], keep="incoming")
    assert resolved["target"] == "模型对象"
    assert resolved["status"] == "edited"


def test_update_entry_marks_edited_and_blocks_duplicate_key(workbench_db) -> None:
    glossary = import_text(CSV_BASIC)
    entries = {
        entry["source"]: entry for entry in glossaries.list_entries(glossary["id"])["items"]
    }
    updated = glossaries.update_entry(
        entries["模型"]["id"], target="AI 模型", tgt_lng="zh"
    )
    assert updated["target"] == "AI 模型"
    assert updated["status"] == "edited"
    # 改成与另一条相同的源词（同语言）应被拒绝
    with pytest.raises(glossaries.GlossaryError):
        glossaries.update_entry(entries["模型"]["id"], source="神经网络", target="x")


def test_bulk_status_and_manual_conflict_rejected(workbench_db) -> None:
    glossary = import_text(CSV_BASIC)
    ids = [entry["id"] for entry in glossaries.list_entries(glossary["id"])["items"]]
    assert glossaries.set_status(ids, "approved") == 2
    with pytest.raises(glossaries.GlossaryError):
        glossaries.set_status(ids, "conflict")


def test_version_requires_no_conflicts(workbench_db) -> None:
    glossary = import_text(CSV_BASIC)
    glossaries.import_rows(
        glossaries.get_glossary(glossary["id"]), glossaries.parse_csv(CSV_CONFLICT.encode())
    )
    with pytest.raises(glossaries.GlossaryError) as excinfo:
        glossaries.create_version(glossary["id"])
    assert "冲突" in str(excinfo.value)


def test_version_snapshot_is_written_and_numbered(workbench_db) -> None:
    glossary = import_text(CSV_BASIC)
    first = glossaries.create_version(glossary["id"], note="初次审核")
    assert first["version"] == 1
    assert first["entry_count"] == 2
    snapshot = Path(first["snapshot_path"])
    assert snapshot.is_file()
    content = snapshot.read_text(encoding="utf-8-sig")
    assert content.startswith("source,target,tgt_lng")
    assert "神经网络,neural network,zh" in content

    second = glossaries.create_version(glossary["id"])
    assert second["version"] == 2
    assert Path(second["snapshot_path"]) != snapshot
    # 旧快照保持不可变
    assert snapshot.read_text(encoding="utf-8-sig") == content


def test_version_for_task_detects_missing_snapshot(workbench_db) -> None:
    glossary = import_text(CSV_BASIC)
    version = glossaries.create_version(glossary["id"])
    Path(version["snapshot_path"]).unlink()
    with pytest.raises(glossaries.GlossaryError):
        glossaries.version_for_task(version["id"])


def test_export_roundtrip(workbench_db) -> None:
    glossary = import_text(CSV_BASIC)
    filename, content = glossaries.export_csv(glossary["id"])
    assert filename.endswith(".glossary.csv")
    rows = glossaries.parse_csv(content.encode("utf-8-sig"))
    assert {row["source"] for row in rows} == {"神经网络", "模型"}
    reimported = glossaries.import_from_file(
        io.BytesIO(content.encode("utf-8-sig")), name="再导入", tgt_lng="zh"
    )
    assert reimported["entry_count"] == 2


def test_import_from_task_reads_glossary_output(workbench_db) -> None:
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
    (dirs["output"] / "sample.zh.glossary.csv").write_text(
        CSV_BASIC, encoding="utf-8-sig"
    )
    task_store.finalize_new_task(task, input_path=input_path, dirs=dirs)

    result = glossaries.import_from_task(task.id)
    assert result["entry_count"] == 2
    assert result["tgt_lng"] == "zh"
    assert result["source_task_id"] == task.id
    assert glossaries.glossary_versions_of_task(task.id) == result["id"]


def test_import_from_task_without_output_is_rejected(workbench_db) -> None:
    task = task_store.create_task(
        input_name="empty.pdf",
        stored_input_path=Path("."),
        output_dir=Path("."),
        work_dir=Path("."),
        log_dir=Path("."),
        api_profile_id=None,
        params_snapshot={},
    )
    dirs = ensure_task_dirs(task.id)
    input_path = dirs["input"] / "empty.pdf"
    input_path.write_bytes(b"%PDF-1.4")
    task_store.finalize_new_task(task, input_path=input_path, dirs=dirs)
    with pytest.raises(glossaries.GlossaryError):
        glossaries.import_from_task(task.id)


def test_queue_passes_glossary_snapshot_to_engine(workbench_db, fake_keyring) -> None:
    from babeldoc_workbench.services import api_profiles

    profile = api_profiles.create_profile(
        name="术语测试", base_url="https://example.test/v1", model="m", api_key="sk-fake"
    )
    glossary = import_text(CSV_BASIC)
    version = glossaries.create_version(glossary["id"])

    captured: dict = {}

    def runner(*, request_dict, **kwargs):
        captured.update(request_dict)
        return make_fake_runner()(request_dict=request_dict, **kwargs)

    queue = TaskQueue(
        runner=runner,
        config=QueueConfig(poll_interval=0.05, db_event_interval=0.05, sse_event_interval=0.0),
    )
    queue.start()
    try:
        task = task_store.create_task(
            input_name="glossary.pdf",
            stored_input_path=Path("."),
            output_dir=Path("."),
            work_dir=Path("."),
            log_dir=Path("."),
            api_profile_id=profile["id"],
            params_snapshot={"lang_out": "zh", "glossary_version_id": version["id"]},
            glossary_version_id=version["id"],
        )
        dirs = ensure_task_dirs(task.id)
        input_path = dirs["input"] / "glossary.pdf"
        input_path.write_bytes(b"%PDF-1.4")
        task_store.finalize_new_task(task, input_path=input_path, dirs=dirs)
        queue.submit(task.id)
        import time

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if task_store.get_task(task.id).status in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.05)
    finally:
        queue.stop()

    assert captured.get("glossary_files") == (version["snapshot_path"],)
    assert task_store.get_task(task.id).status == "succeeded"
