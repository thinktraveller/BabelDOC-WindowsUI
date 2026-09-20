"""应用数据库表定义（步骤 4 需要的部分）。"""

from __future__ import annotations

from datetime import datetime

from peewee import BooleanField, CharField, DateTimeField, Model, TextField

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


ALL_TABLES = (AppSetting, ApiProfile, ParamPreset)
