"""pytest 配置：让 ``desktop/tests`` 能直接导入后端包。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def workbench_db(tmp_path: Path):
    """每个测试使用独立的临时 SQLite 应用库。"""
    from babeldoc_workbench import db

    db.init(tmp_path / "db")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def fake_keyring(monkeypatch):
    """内存版凭据存储；测试中不触碰真实 Windows 凭据管理器。"""
    from babeldoc_workbench import security

    store: dict[tuple[str, str], str] = {}

    class InMemoryKeyring:
        @staticmethod
        def set_password(service: str, ref: str, value: str) -> None:
            store[(service, ref)] = value

        @staticmethod
        def get_password(service: str, ref: str) -> str | None:
            return store.get((service, ref))

        @staticmethod
        def delete_password(service: str, ref: str) -> None:
            store.pop((service, ref), None)

        @staticmethod
        def get_keyring():
            class _Backend:
                name = "in-memory"

            return _Backend()

    keyring = InMemoryKeyring()
    monkeypatch.setattr(security, "_import_keyring", lambda: keyring)
    return store
