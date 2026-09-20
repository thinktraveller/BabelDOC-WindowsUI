"""API 配置管理：增删改查、默认项、连接测试。

Key 只经 :mod:`babeldoc_workbench.security` 写入系统凭据存储；数据库只保存
``credential_ref``，接口响应与日志都不包含明文 Key。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from peewee import IntegrityError

from babeldoc_workbench import security
from babeldoc_workbench.db import database
from babeldoc_workbench.engine.protocol import EngineApiConfig
from babeldoc_workbench.logging_setup import redact
from babeldoc_workbench.models import ApiProfile
from babeldoc_workbench.services.connection_test import (
    DEFAULT_TIMEOUT_SECONDS,
    test_connection,
)

logger = logging.getLogger(__name__)


class ProfileError(RuntimeError):
    """API 配置操作失败（名称重复、字段不合法等）。"""


class ProfileNotFound(ProfileError):
    pass


def _new_credential_ref() -> str:
    return f"profile-{uuid.uuid4().hex}"


def _public(profile: ApiProfile) -> dict[str, Any]:
    """对外表示；只暴露是否已保存 Key，不暴露凭据引用本身。"""
    has_key = bool(profile.credential_ref) and bool(
        _safe_load_key(profile.credential_ref)
    )
    return {
        "id": profile.id,
        "name": profile.name,
        "base_url": profile.base_url,
        "model": profile.model,
        "reasoning": profile.reasoning,
        "thinking": profile.thinking,
        "is_default": bool(profile.is_default),
        "has_key": has_key,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def _safe_load_key(credential_ref: str | None) -> str | None:
    if not credential_ref:
        return None
    try:
        return security.load_key(credential_ref)
    except security.CredentialStoreError as exc:
        logger.warning("读取凭据失败：%s", redact(str(exc)))
        return None


def list_profiles() -> list[dict[str, Any]]:
    query = ApiProfile.select().order_by(ApiProfile.is_default.desc(), ApiProfile.name)
    return [_public(profile) for profile in query]


def get_profile(profile_id: int) -> dict[str, Any]:
    profile = ApiProfile.get_or_none(ApiProfile.id == profile_id)
    if profile is None:
        raise ProfileNotFound(f"找不到 API 配置：{profile_id}")
    return _public(profile)


def create_profile(
    *,
    name: str,
    base_url: str,
    model: str,
    reasoning: str | None = None,
    thinking: str | None = None,
    api_key: str | None = None,
    make_default: bool = False,
) -> dict[str, Any]:
    name = (name or "").strip()
    base_url = (base_url or "").strip()
    model = (model or "").strip()
    if not name:
        raise ProfileError("配置名称不能为空")
    if not base_url:
        raise ProfileError("Base URL 不能为空")
    if not model:
        raise ProfileError("模型名不能为空")

    credential_ref = None
    if api_key:
        credential_ref = _new_credential_ref()
        security.save_key(credential_ref, api_key)

    with database.atomic():
        if make_default or not ApiProfile.select().count():
            ApiProfile.update(is_default=False).execute()
            make_default = True
        try:
            profile = ApiProfile.create(
                name=name,
                base_url=base_url,
                model=model,
                reasoning=(reasoning or None),
                thinking=(thinking or None),
                credential_ref=credential_ref,
                is_default=make_default,
            )
        except IntegrityError as exc:
            if credential_ref:
                _safe_delete_key(credential_ref)
            raise ProfileError(f"配置名称已存在：{name}") from exc
    logger.info("新增 API 配置：%s（默认=%s）", profile.name, make_default)
    return _public(profile)


def update_profile(
    profile_id: int,
    *,
    name: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    reasoning: str | None = None,
    thinking: str | None = None,
    api_key: str | None = None,
    make_default: bool | None = None,
) -> dict[str, Any]:
    profile = ApiProfile.get_or_none(ApiProfile.id == profile_id)
    if profile is None:
        raise ProfileNotFound(f"找不到 API 配置：{profile_id}")
    with database.atomic():
        if name is not None:
            profile.name = name.strip()
            if not profile.name:
                raise ProfileError("配置名称不能为空")
        if base_url is not None:
            profile.base_url = base_url.strip()
            if not profile.base_url:
                raise ProfileError("Base URL 不能为空")
        if model is not None:
            profile.model = model.strip()
            if not profile.model:
                raise ProfileError("模型名不能为空")
        if reasoning is not None:
            profile.reasoning = reasoning or None
        if thinking is not None:
            profile.thinking = thinking or None
        if api_key:
            if not profile.credential_ref:
                profile.credential_ref = _new_credential_ref()
            security.save_key(profile.credential_ref, api_key)
        if make_default:
            ApiProfile.update(is_default=False).where(
                ApiProfile.id != profile.id
            ).execute()
            profile.is_default = True
        try:
            profile.save()
        except IntegrityError as exc:
            raise ProfileError("配置名称已存在") from exc
    logger.info("更新 API 配置：%s", profile.name)
    return _public(profile)


def set_default(profile_id: int) -> dict[str, Any]:
    profile = ApiProfile.get_or_none(ApiProfile.id == profile_id)
    if profile is None:
        raise ProfileNotFound(f"找不到 API 配置：{profile_id}")
    with database.atomic():
        ApiProfile.update(is_default=False).execute()
        profile.is_default = True
        profile.save()
    return _public(profile)


def _safe_delete_key(credential_ref: str | None) -> None:
    if not credential_ref:
        return
    try:
        security.delete_key(credential_ref)
    except security.CredentialStoreError as exc:
        logger.warning("删除凭据失败：%s", redact(str(exc)))


def delete_profile(profile_id: int) -> None:
    profile = ApiProfile.get_or_none(ApiProfile.id == profile_id)
    if profile is None:
        raise ProfileNotFound(f"找不到 API 配置：{profile_id}")
    credential_ref = profile.credential_ref
    with database.atomic():
        profile.delete_instance()
        remaining = ApiProfile.select().order_by(ApiProfile.id)
        if remaining.count() and not ApiProfile.select().where(
            ApiProfile.is_default == True  # noqa: E712 - peewee 表达式
        ).count():
            first = remaining.first()
            first.is_default = True
            first.save()
    _safe_delete_key(credential_ref)
    logger.info("删除 API 配置：%s", profile.name)


def resolve_api_config(profile_id: int) -> EngineApiConfig:
    """把配置解析为引擎所需的 API 配置；Key 从系统凭据存储读取。"""
    profile = ApiProfile.get_or_none(ApiProfile.id == profile_id)
    if profile is None:
        raise ProfileNotFound(f"找不到 API 配置：{profile_id}")
    return EngineApiConfig(
        model=profile.model,
        base_url=profile.base_url,
        api_key=_safe_load_key(profile.credential_ref),
        reasoning=profile.reasoning,
        thinking=profile.thinking,
    )


def test_profile(
    profile_id: int, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """对已保存的配置做一次连接测试，结果按类别返回给界面。"""
    profile = ApiProfile.get_or_none(ApiProfile.id == profile_id)
    if profile is None:
        raise ProfileNotFound(f"找不到 API 配置：{profile_id}")
    result = test_connection(
        base_url=profile.base_url,
        model=profile.model,
        api_key=_safe_load_key(profile.credential_ref),
        timeout=timeout,
    )
    if "detail" in result:
        result["detail"] = redact(str(result["detail"]))
    result["profile_id"] = profile.id
    result["profile_name"] = profile.name
    logger.info(
        "连接测试：%s → %s", profile.name, result.get("category")
    )
    return result
