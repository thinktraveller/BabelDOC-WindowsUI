"""单实例锁的离线单元测试。"""

from __future__ import annotations

from pathlib import Path

from babeldoc_workbench.single_instance import SingleInstance


def test_second_acquire_in_same_process_fails(tmp_path: Path) -> None:
    name = f"Local\\BabelDOC-Test-{tmp_path.name}"
    first = SingleInstance(name=name, lock_dir=tmp_path)
    second = SingleInstance(name=name, lock_dir=tmp_path)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        first.release()
        second.release()


def test_lock_released_allows_reacquire(tmp_path: Path) -> None:
    name = f"Local\\BabelDOC-Test-Reuse-{tmp_path.name}"
    first = SingleInstance(name=name, lock_dir=tmp_path)
    assert first.acquire() is True
    first.release()
    second = SingleInstance(name=name, lock_dir=tmp_path)
    try:
        assert second.acquire() is True
    finally:
        second.release()


def test_acquired_flag_reflects_state(tmp_path: Path) -> None:
    name = f"Local\\BabelDOC-Test-Flag-{tmp_path.name}"
    instance = SingleInstance(name=name, lock_dir=tmp_path)
    assert instance.acquired is False
    assert instance.acquire() is True
    assert instance.acquired is True
    instance.release()
    assert instance.acquired is False
