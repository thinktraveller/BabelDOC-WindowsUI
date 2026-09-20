"""SQLite 连接与应用库初始化。

约定（计划书「数据存储方案」）：应用库放在应用数据目录的 ``db`` 子目录，与引擎
缓存库分开，使用 WAL 模式，由服务进程单写。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from peewee import SqliteDatabase

logger = logging.getLogger(__name__)

DB_FILENAME = "workbench.db"
SCHEMA_VERSION = 2

database = SqliteDatabase(None)
MIGRATIONS: dict[int, Callable[[], None]] = {}


def migration(version: int):
    """注册某一版本的迁移函数。"""

    def decorator(func: Callable[[], None]) -> Callable[[], None]:
        MIGRATIONS[version] = func
        return func

    return decorator


@migration(2)
def _migrate_glossary_tables() -> None:
    """v1 → v2：升级前创建的库没有术语表相关表与唯一索引，这里补齐。"""
    from babeldoc_workbench import models

    database.create_tables(
        (models.Glossary, models.GlossaryEntry, models.GlossaryVersion), safe=True
    )
    database.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS glossary_entries_key "
        "ON glossary_entries (glossary_id, source, tgt_lng)"
    )


def db_path(app_db_dir: Path) -> Path:
    return Path(app_db_dir) / DB_FILENAME


def init(app_db_dir: Path) -> SqliteDatabase:
    """绑定并连接应用数据库，按需建表。"""
    path = db_path(app_db_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if database.is_closed():
        database.init(
            str(path),
            pragmas={
                "journal_mode": "wal",
                "foreign_keys": 1,
                "busy_timeout": 5000,
                "synchronous": 1,
            },
        )
    database.connect(reuse_if_open=True)
    from babeldoc_workbench import models  # noqa: F401 - 绑定表定义

    database.create_tables(models.ALL_TABLES, safe=True)
    _write_schema_version()
    migrate()
    logger.info("应用数据库就绪：%s", path)
    return database


def _write_schema_version() -> None:
    from babeldoc_workbench.models import AppSetting

    AppSetting.get_or_create(
        key="schema_version", defaults={"value": str(SCHEMA_VERSION)}
    )


def schema_version() -> str:
    from babeldoc_workbench.models import AppSetting

    row = AppSetting.get_or_none(AppSetting.key == "schema_version")
    return row.value if row else ""


def _set_schema_version(value: int) -> None:
    from babeldoc_workbench.models import AppSetting

    row, created = AppSetting.get_or_create(
        key="schema_version", defaults={"value": str(value)}
    )
    if not created:
        row.value = str(value)
        row.save()


def migrate() -> list[int]:
    """按 ``schema_version`` 顺序执行未应用的迁移，返回本次应用的版本列表。"""
    stored = schema_version()
    current = int(stored) if str(stored).isdigit() else SCHEMA_VERSION
    applied: list[int] = []
    for version in range(current + 1, SCHEMA_VERSION + 1):
        migration_func = MIGRATIONS.get(version)
        if migration_func is not None:
            logger.info("执行数据库迁移 → v%s", version)
            migration_func()
        applied.append(version)
    if applied:
        _set_schema_version(SCHEMA_VERSION)
    return applied


def close() -> None:
    if not database.is_closed():
        database.close()


@contextmanager
def transaction():
    with database.atomic():
        yield
