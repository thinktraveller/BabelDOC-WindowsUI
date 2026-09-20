"""应用数据库表定义（步骤 4 需要的部分）。"""

from __future__ import annotations

from datetime import datetime

from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    TextField,
)

from babeldoc_workbench.db import database


class BaseModel(Model):
    class Meta:
        database = database


class AppSetting(BaseModel):
    """键值设置表，同时承载 ``schema_version``。"""

    key = CharField(primary_key=True)
    value = TextField(default="")
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "app_settings"


class ApiProfile(BaseModel):
    """API 配置；不含明文 Key，只保存凭据引用。"""

    name = CharField(unique=True)
    base_url = CharField()
    model = CharField()
    reasoning = CharField(null=True)
    thinking = CharField(null=True)
    credential_ref = CharField(null=True)
    is_default = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "api_profiles"


class ParamPreset(BaseModel):
    """参数预设；只保存参数 JSON，不含任何 Key 字段。"""

    name = CharField(unique=True)
    params_json = TextField()
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "param_presets"


TASK_STATUS_QUEUED = "queued"
TASK_STATUS_PREPARING = "preparing"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCEEDED = "succeeded"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELLED = "cancelled"
TASK_STATUS_INTERRUPTED = "interrupted"

TASK_ACTIVE_STATUSES = (TASK_STATUS_PREPARING, TASK_STATUS_RUNNING)
TASK_TERMINAL_STATUSES = (
    TASK_STATUS_SUCCEEDED,
    TASK_STATUS_FAILED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_INTERRUPTED,
)
TASK_STATUSES = (
    TASK_STATUS_QUEUED,
    *TASK_ACTIVE_STATUSES,
    *TASK_TERMINAL_STATUSES,
)

OUTPUT_KINDS = ("mono", "dual", "glossary", "log")


class Task(BaseModel):
    """一次翻译任务；参数以快照形式冻结，运行中不可变更。"""

    status = CharField(default=TASK_STATUS_QUEUED, index=True)
    input_name = CharField()
    stored_input_path = CharField()
    output_dir = CharField()
    work_dir = CharField()
    log_dir = CharField()
    api_profile_id = IntegerField(null=True)
    params_snapshot_json = TextField(default="{}")
    glossary_version_id = IntegerField(null=True)
    engine_version = CharField(null=True)
    stage = CharField(null=True)
    progress = FloatField(null=True)
    error_code = CharField(null=True)
    error_message = TextField(null=True)
    cancel_requested = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.now)
    started_at = DateTimeField(null=True)
    finished_at = DateTimeField(null=True)

    class Meta:
        table_name = "tasks"


class TaskOutput(BaseModel):
    """成果索引；``exists`` 用于发现被移动或删除的成果。"""

    task = ForeignKeyField(Task, backref="outputs", on_delete="CASCADE")
    kind = CharField()
    path = CharField()
    size = IntegerField(default=0)
    exists = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "task_outputs"


class TaskEvent(BaseModel):
    """任务事件（滚动保留最近若干条）。"""

    task = ForeignKeyField(Task, backref="events", on_delete="CASCADE")
    sequence = IntegerField()
    ts = DateTimeField(default=datetime.now)
    type = CharField()
    payload_json = TextField(default="{}")

    class Meta:
        table_name = "task_events"


GLOSSARY_STATUS_NEW = "new"
GLOSSARY_STATUS_EDITED = "edited"
GLOSSARY_STATUS_APPROVED = "approved"
GLOSSARY_STATUS_CONFLICT = "conflict"
GLOSSARY_STATUSES = (
    GLOSSARY_STATUS_NEW,
    GLOSSARY_STATUS_EDITED,
    GLOSSARY_STATUS_APPROVED,
    GLOSSARY_STATUS_CONFLICT,
)


class Glossary(BaseModel):
    """术语资料库；``source_task_id`` 记录它是从哪个任务的提取结果导入的。"""

    name = CharField()
    source_task_id = IntegerField(null=True)
    tgt_lng = CharField()
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "glossaries"


class GlossaryEntry(BaseModel):
    """术语条目；“源词 + 目标语言”是唯一键，冲突时保留另一候选值而不覆盖。"""

    glossary = ForeignKeyField(Glossary, backref="entries", on_delete="CASCADE")
    source = CharField()
    target = CharField()
    tgt_lng = CharField()
    status = CharField(default=GLOSSARY_STATUS_NEW)
    conflict_target = CharField(null=True)
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "glossary_entries"
        indexes = ((("glossary", "source", "tgt_lng"), True),)


class GlossaryVersion(BaseModel):
    """不可变版本快照；任务提交时绑定这里的 id。"""

    glossary = ForeignKeyField(Glossary, backref="versions", on_delete="CASCADE")
    version = IntegerField()
    snapshot_path = CharField()
    note = CharField(null=True)
    entry_count = IntegerField(default=0)
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "glossary_versions"
        indexes = ((("glossary", "version"), True),)


ALL_TABLES = (
    AppSetting,
    ApiProfile,
    ParamPreset,
    Task,
    TaskOutput,
    TaskEvent,
    Glossary,
    GlossaryEntry,
    GlossaryVersion,
)
