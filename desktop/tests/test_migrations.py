"""数据库迁移的离线测试。"""

from __future__ import annotations

from pathlib import Path

from peewee import SqliteDatabase

from babeldoc_workbench import db
from babeldoc_workbench.models import AppSetting, Glossary, GlossaryEntry
from babeldoc_workbench.settings import ensure_app_dirs


def test_fresh_database_gets_current_schema(tmp_path: Path) -> None:
    paths = ensure_app_dirs(tmp_path / "appdata")
    db.init(paths.db)
    try:
        assert db.schema_version() == str(db.SCHEMA_VERSION)
        tables = {row[0] for row in db.database.execute_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"tasks", "glossary_entries", "glossary_versions"} <= tables
    finally:
        db.close()


def test_legacy_database_is_upgraded_without_losing_rows(tmp_path: Path) -> None:
    paths = ensure_app_dirs(tmp_path / "appdata")
    legacy = SqliteDatabase(str(db.db_path(paths.db)))
    legacy.connect()
    legacy.execute_sql(
        "CREATE TABLE app_settings (key VARCHAR(255) PRIMARY KEY, value TEXT NOT NULL, "
        "updated_at DATETIME NOT NULL)"
    )
    legacy.execute_sql(
        "INSERT INTO app_settings (key, value, updated_at) VALUES "
        "('schema_version', '1', '2026-09-20 10:00:00'), "
        "('default_output_dir', 'D:/译文', '2026-09-20 10:00:00')"
    )
    legacy.execute_sql(
        "CREATE TABLE api_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(255) "
        "NOT NULL UNIQUE, base_url VARCHAR(255) NOT NULL, model VARCHAR(255) NOT NULL, "
        "reasoning VARCHAR(255), thinking VARCHAR(255), credential_ref VARCHAR(255), "
        "is_default INTEGER NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
    )
    legacy.execute_sql(
        "INSERT INTO api_profiles (name, base_url, model, is_default, created_at, updated_at) "
        "VALUES ('旧配置', 'https://old.test/v1', 'old-model', 1, "
        "'2026-09-20 10:00:00', '2026-09-20 10:00:00')"
    )
    legacy.close()

    db.init(paths.db)
    try:
        assert db.schema_version() == str(db.SCHEMA_VERSION)
        assert AppSetting.get(AppSetting.key == "default_output_dir").value == "D:/译文"
        assert Glossary.select().count() == 0
        # 迁移后术语表可用（唯一索引存在）
        glossary = Glossary.create(name="迁移后新建", tgt_lng="zh")
        GlossaryEntry.create(glossary=glossary, source="a", target="b", tgt_lng="zh")
        assert GlossaryEntry.select().count() == 1
    finally:
        db.close()
