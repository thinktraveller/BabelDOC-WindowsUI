"""环境自检模块的离线单元测试（不访问网络、不依赖真实资源）。"""

from __future__ import annotations

from pathlib import Path

from babeldoc_workbench.engine.selfcheck import (
    STATUS_FAIL,
    STATUS_OK,
    STATUS_WARN,
    CheckResult,
    check_assets_inventory,
    check_directory_writable,
    check_platform,
    check_python_runtime,
    overall_status,
    sha3_256_of_file,
)


def test_python_runtime_accepts_supported_version() -> None:
    result = check_python_runtime((3, 13, 2), executable="python.exe", frozen=False)
    assert result.status == STATUS_OK
    assert "源码运行" in result.detail


def test_python_runtime_rejects_old_version() -> None:
    assert check_python_runtime((3, 11, 9)).status == STATUS_FAIL


def test_python_runtime_rejects_too_new_version() -> None:
    assert check_python_runtime((3, 14, 0)).status == STATUS_FAIL


def test_python_runtime_mentions_frozen_mode() -> None:
    result = check_python_runtime((3, 13, 0), frozen=True)
    assert "冻结版" in result.detail


def test_platform_warns_outside_windows() -> None:
    assert check_platform("Linux", "x86_64", pointer_size=8).status == STATUS_WARN
    assert check_platform("Windows", "AMD64", pointer_size=8).status == STATUS_OK


def test_platform_fails_on_32bit_python() -> None:
    result = check_platform("Windows", "x86", pointer_size=4)
    assert result.status == STATUS_FAIL
    assert "64 位" in result.hint


def test_directory_writable_detects_ok(tmp_path: Path) -> None:
    result = check_directory_writable(tmp_path / "BabelDOC Workbench", create=True)
    assert result.status == STATUS_OK
    assert (tmp_path / "BabelDOC Workbench").is_dir()


def test_directory_writable_reports_missing_parent(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-drive" / "x" / "y"
    assert check_directory_writable(missing).status == STATUS_FAIL


def test_assets_inventory_detects_missing_files(tmp_path: Path) -> None:
    inventory = {
        "models": [{"name": "doclayout.onnx", "sha3_256": "deadbeef"}],
        "fonts": [{"name": "font.ttf", "sha3_256": "deadbeef"}],
    }
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "doclayout.onnx").write_bytes(b"x")
    (tmp_path / "fonts").mkdir()
    results = check_assets_inventory(inventory, tmp_path)
    by_key = {item.key: item for item in results}
    assert by_key["assets_models"].status == STATUS_OK
    assert by_key["assets_fonts"].status == STATUS_FAIL
    assert "字体" in by_key["assets_fonts"].hint or by_key["assets_fonts"].hint


def test_assets_inventory_verifies_hash_when_requested(tmp_path: Path) -> None:
    sample = tmp_path / "models" / "doclayout.onnx"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"content")
    good = sha3_256_of_file(sample)
    inventory = {"models": [{"name": "doclayout.onnx", "sha3_256": good}]}
    assert check_assets_inventory(inventory, tmp_path, verify_hashes=True)[0].status == STATUS_OK

    bad_inventory = {"models": [{"name": "doclayout.onnx", "sha3_256": "00" * 32}]}
    result = check_assets_inventory(bad_inventory, tmp_path, verify_hashes=True)[0]
    assert result.status == STATUS_FAIL


def test_assets_inventory_skips_empty_groups(tmp_path: Path) -> None:
    assert check_assets_inventory({"fonts": []}, tmp_path) == []


def test_overall_status_precedence() -> None:
    ok = CheckResult("a", "A", STATUS_OK, "")
    warn = CheckResult("b", "B", STATUS_WARN, "")
    fail = CheckResult("c", "C", STATUS_FAIL, "")
    assert overall_status([ok]) == STATUS_OK
    assert overall_status([ok, warn]) == STATUS_WARN
    assert overall_status([ok, warn, fail]) == STATUS_FAIL
