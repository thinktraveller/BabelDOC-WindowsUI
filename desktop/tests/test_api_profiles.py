"""API 配置管理的离线单元测试（内存凭据存储 + 临时数据库）。"""

from __future__ import annotations

import pytest

from babeldoc_workbench import security
from babeldoc_workbench.services import api_profiles

FAKE_KEY = "sk-fake-0123456789abcdef"


def test_create_profile_stores_key_in_keyring(workbench_db, fake_keyring) -> None:
    profile = api_profiles.create_profile(
        name="默认服务",
        base_url="https://example.test/v1",
        model="demo-model",
        api_key=FAKE_KEY,
        make_default=True,
    )
    assert profile["has_key"] is True
    assert profile["is_default"] is True
    assert FAKE_KEY not in str(profile)
    assert profile["name"] == "默认服务"
    # Key 只存在于凭据存储，数据库里只有引用
    from babeldoc_workbench.models import ApiProfile

    row = ApiProfile.get(ApiProfile.id == profile["id"])
    assert row.credential_ref and FAKE_KEY not in (row.credential_ref or "")
    assert any(FAKE_KEY == value for value in fake_keyring.values())


def test_first_profile_becomes_default(workbench_db, fake_keyring) -> None:
    profile = api_profiles.create_profile(
        name="第一项", base_url="https://a.test/v1", model="m1"
    )
    assert profile["is_default"] is True
    assert profile["has_key"] is False


def test_duplicate_name_is_rejected(workbench_db, fake_keyring) -> None:
    api_profiles.create_profile(name="重复", base_url="https://a.test/v1", model="m")
    with pytest.raises(api_profiles.ProfileError):
        api_profiles.create_profile(name="重复", base_url="https://b.test/v1", model="m")


def test_missing_required_fields_are_rejected(workbench_db, fake_keyring) -> None:
    with pytest.raises(api_profiles.ProfileError):
        api_profiles.create_profile(name=" ", base_url="https://a.test/v1", model="m")
    with pytest.raises(api_profiles.ProfileError):
        api_profiles.create_profile(name="x", base_url="", model="m")
    with pytest.raises(api_profiles.ProfileError):
        api_profiles.create_profile(name="x", base_url="https://a.test/v1", model="")


def test_update_profile_replaces_key(workbench_db, fake_keyring) -> None:
    profile = api_profiles.create_profile(
        name="可更新", base_url="https://a.test/v1", model="m1", api_key="sk-fake-old-key"
    )
    updated = api_profiles.update_profile(
        profile["id"],
        model="m2",
        api_key="sk-fake-new-key",
        reasoning="low",
    )
    assert updated["model"] == "m2"
    assert updated["reasoning"] == "low"
    assert "sk-fake-new-key" in fake_keyring.values()
    assert "sk-fake-old-key" not in fake_keyring.values()
    config = api_profiles.resolve_api_config(profile["id"])
    assert config.api_key == "sk-fake-new-key"
    assert config.model == "m2"


def test_set_default_switches_flag(workbench_db, fake_keyring) -> None:
    first = api_profiles.create_profile(
        name="A", base_url="https://a.test/v1", model="m", make_default=True
    )
    second = api_profiles.create_profile(name="B", base_url="https://b.test/v1", model="m")
    api_profiles.set_default(second["id"])
    profiles = {item["id"]: item for item in api_profiles.list_profiles()}
    assert profiles[first["id"]]["is_default"] is False
    assert profiles[second["id"]]["is_default"] is True


def test_delete_profile_removes_key_and_reassigns_default(
    workbench_db, fake_keyring
) -> None:
    first = api_profiles.create_profile(
        name="A", base_url="https://a.test/v1", model="m", api_key="sk-fake-a-key"
    )
    second = api_profiles.create_profile(
        name="B", base_url="https://b.test/v1", model="m", make_default=True
    )
    api_profiles.delete_profile(second["id"])
    assert "sk-fake-a-key" in fake_keyring.values()
    remaining = api_profiles.list_profiles()
    assert len(remaining) == 1
    assert remaining[0]["id"] == first["id"]
    assert remaining[0]["is_default"] is True


def test_missing_profile_raises(workbench_db, fake_keyring) -> None:
    with pytest.raises(api_profiles.ProfileNotFound):
        api_profiles.get_profile(999)
    with pytest.raises(api_profiles.ProfileNotFound):
        api_profiles.test_profile(999)


def test_test_profile_returns_category(workbench_db, fake_keyring, monkeypatch) -> None:
    profile = api_profiles.create_profile(
        name="测试项", base_url="https://a.test/v1", model="m", api_key="sk-fake-x-key"
    )

    def fake_test_connection(*, base_url, model, api_key, timeout):
        assert api_key == "sk-fake-x-key"
        return {
            "ok": False,
            "category": "auth_failed",
            "message": "认证失败：请检查 API Key 是否正确、是否已过期",
            "detail": f"AuthenticationError: 使用了 {api_key}",
            "model": model,
        }

    monkeypatch.setattr(api_profiles, "test_connection", fake_test_connection)
    result = api_profiles.test_profile(profile["id"])
    assert result["category"] == "auth_failed"
    assert result["profile_id"] == profile["id"]
    # 技术详情中的密钥必须被脱敏
    assert "sk-fake-x-key" not in result["detail"]


def test_credentials_unavailable_raises(workbench_db, monkeypatch) -> None:
    def broken():
        raise RuntimeError("no keyring backend")

    monkeypatch.setattr(security, "_import_keyring", broken)
    with pytest.raises(security.CredentialStoreError):
        api_profiles.create_profile(
            name="无凭据服务",
            base_url="https://a.test/v1",
            model="m",
            api_key="sk-fake-key-value",
        )
