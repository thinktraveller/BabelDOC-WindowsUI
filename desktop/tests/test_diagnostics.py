"""诊断包导出的离线测试（使用假凭据，不涉及真实 Key）。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from babeldoc_workbench.services import diagnostics, task_store
from babeldoc_workbench.services.task_queue import ensure_task_dirs

FAKE_KEY = "sk-fake-diagnostics-0123456789"


def make_task_with_secret_in_log(workbench_db) -> "object":
    task = task_store.create_task(
        input_name="秘密文档.pdf",
        stored_input_path=Path("."),
        output_dir=Path("."),
        work_dir=Path("."),
        log_dir=Path("."),
        api_profile_id=None,
        params_snapshot={"lang_out": "zh", "pages": "1-2"},
    )
    dirs = ensure_task_dirs(task.id)
    input_path = dirs["input"] / "秘密文档.pdf"
    input_path.write_bytes(b"%PDF-1.4")
    task_store.finalize_new_task(task, input_path=input_path, dirs=dirs)
    return task


def test_export_contains_expected_entries(workbench_db, tmp_path: Path) -> None:
    make_task_with_secret_in_log(workbench_db)
    result = diagnostics.export_diagnostics(tmp_path)
    package = Path(result["path"])
    assert package.is_file() and package.parent == tmp_path
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        assert {"summary.json", "tasks.json", "resources.json", "logs/workbench.log"} <= names
        summary = json.loads(archive.read("summary.json"))
        tasks = json.loads(archive.read("tasks.json"))
    assert summary["app_version"]
    assert summary["schema_version"]
    assert tasks[0]["input_name"] == "秘密文档.pdf"
    assert "params" in tasks[0], "参数快照应包含在诊断包里"


def test_export_redacts_secrets_and_flags_findings(
    workbench_db, tmp_path: Path, monkeypatch
) -> None:
    from babeldoc_workbench.services import diagnostics as diagnostics_module

    def fake_log_text() -> str:
        return f"使用 {FAKE_KEY} 调用模型\n"

    monkeypatch.setattr(diagnostics_module, "_log_text", fake_log_text)
    result = diagnostics_module.export_diagnostics(tmp_path)
    with zipfile.ZipFile(result["path"]) as archive:
        log_content = archive.read("logs/workbench.log").decode("utf-8")
    assert FAKE_KEY not in log_content, "导出前必须脱敏"
    assert diagnostics_module.scan_package(Path(result["path"])) == []

    # 扫描器确实能发现问题（构造一个未脱敏的包）
    dirty = tmp_path / "dirty.zip"
    with zipfile.ZipFile(dirty, "w") as archive:
        archive.writestr("logs/workbench.log", f"key={FAKE_KEY}")
    findings = diagnostics_module.scan_package(dirty)
    assert findings, "扫描器应能发现未脱敏的 Key"
