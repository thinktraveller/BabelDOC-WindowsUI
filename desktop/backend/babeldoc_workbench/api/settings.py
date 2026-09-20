"""设置相关接口：API 配置、连接测试、参数校验与预设。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from babeldoc_workbench import security
from babeldoc_workbench.services import api_profiles, params, presets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ProfilePayload(BaseModel):
    name: str
    base_url: str
    model: str
    reasoning: str | None = None
    thinking: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    make_default: bool = False


class ValidatePayload(BaseModel):
    params: dict[str, Any]
    page_count: int | None = None
    glossary_tgt_lng: str | None = None


class PresetPayload(BaseModel):
    name: str
    params: dict[str, Any]


def _profile_error(exc: Exception) -> HTTPException:
    status = 404 if isinstance(exc, api_profiles.ProfileNotFound) else 400
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/params/schema")
def params_schema() -> dict[str, Any]:
    return {
        "groups": [
            {"id": "common", "label": "常用"},
            {"id": "advanced", "label": "高级"},
        ],
        "items": params.schema_for_ui(),
        "defaults": params.default_params(),
    }


@router.post("/params/validate")
def validate(payload: ValidatePayload) -> dict[str, Any]:
    glossary = (
        {"tgt_lng": payload.glossary_tgt_lng} if payload.glossary_tgt_lng else None
    )
    result = params.validate_params(
        payload.params, page_count=payload.page_count, glossary=glossary
    )
    return {
        "ok": result.ok,
        "errors": result.errors,
        "params": result.params,
        "engine_fields": params.to_engine_fields(result.params) if result.ok else {},
    }


@router.get("/api-profiles")
def list_profiles() -> dict[str, Any]:
    return {
        "items": api_profiles.list_profiles(),
        "credentials_available": security.credentials_available()[0],
    }


@router.post("/api-profiles", status_code=201)
def create_profile(payload: ProfilePayload) -> dict[str, Any]:
    try:
        return api_profiles.create_profile(
            name=payload.name,
            base_url=payload.base_url,
            model=payload.model,
            reasoning=payload.reasoning,
            thinking=payload.thinking,
            api_key=payload.api_key,
            make_default=payload.make_default,
        )
    except (api_profiles.ProfileError, security.CredentialStoreError) as exc:
        raise _profile_error(exc) from exc


@router.get("/api-profiles/{profile_id}")
def get_profile(profile_id: int) -> dict[str, Any]:
    try:
        return api_profiles.get_profile(profile_id)
    except api_profiles.ProfileError as exc:
        raise _profile_error(exc) from exc


@router.put("/api-profiles/{profile_id}")
def update_profile(profile_id: int, payload: ProfilePayload) -> dict[str, Any]:
    try:
        return api_profiles.update_profile(
            profile_id,
            name=payload.name,
            base_url=payload.base_url,
            model=payload.model,
            reasoning=payload.reasoning,
            thinking=payload.thinking,
            api_key=payload.api_key,
            make_default=payload.make_default,
        )
    except (api_profiles.ProfileError, security.CredentialStoreError) as exc:
        raise _profile_error(exc) from exc


@router.delete("/api-profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: int) -> None:
    try:
        api_profiles.delete_profile(profile_id)
    except api_profiles.ProfileError as exc:
        raise _profile_error(exc) from exc


@router.post("/api-profiles/{profile_id}/default")
def set_default(profile_id: int) -> dict[str, Any]:
    try:
        return api_profiles.set_default(profile_id)
    except api_profiles.ProfileError as exc:
        raise _profile_error(exc) from exc


@router.post("/api-profiles/{profile_id}/test")
def test_profile(profile_id: int) -> dict[str, Any]:
    try:
        return api_profiles.test_profile(profile_id)
    except api_profiles.ProfileError as exc:
        raise _profile_error(exc) from exc


@router.get("/presets")
def list_presets() -> dict[str, Any]:
    return {"items": presets.list_presets()}


@router.post("/presets", status_code=201)
def save_preset(payload: PresetPayload) -> dict[str, Any]:
    try:
        return presets.save_preset(payload.name, payload.params)
    except presets.PresetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/presets/{preset_id}")
def load_preset(preset_id: int) -> dict[str, Any]:
    try:
        return presets.load_preset(preset_id)
    except presets.PresetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/presets/{preset_id}", status_code=204)
def delete_preset(preset_id: int) -> None:
    try:
        presets.delete_preset(preset_id)
    except presets.PresetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
