"""设置接口的端到端测试（走完整鉴权中间件 + 临时数据库 + 内存凭据）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from babeldoc_workbench import security
from babeldoc_workbench.app import SessionInfo, create_app
from babeldoc_workbench.engine.selfcheck import run_light_checks

FAKE_KEY = "sk-fake-api-profile-key"
PORT = 8123
TOKEN = "fake-session-token-for-tests"


@pytest.fixture
def client(workbench_db, fake_keyring):
    session = SessionInfo(token=TOKEN, port=PORT)
    app = create_app(
        lambda deep=False: run_light_checks(), session, lambda: run_light_checks()
    )
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{PORT}",
        headers={security.TOKEN_HEADER: TOKEN},
    ) as test_client:
        yield test_client


def test_requires_token(client: TestClient) -> None:
    response = client.get("/api/settings/api-profiles", headers={security.TOKEN_HEADER: ""})
    assert response.status_code == 403


def test_profile_crud_never_returns_key(client: TestClient) -> None:
    created = client.post(
        "/api/settings/api-profiles",
        json={
            "name": "主服务",
            "base_url": "https://example.test/v1",
            "model": "demo-model",
            "api_key": FAKE_KEY,
            "make_default": True,
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["has_key"] is True
    assert FAKE_KEY not in created.text
    profile_id = payload["id"]

    listed = client.get("/api/settings/api-profiles")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["name"] == "主服务"
    assert listed.json()["credentials_available"] is True
    assert FAKE_KEY not in listed.text

    updated = client.put(
        f"/api/settings/api-profiles/{profile_id}",
        json={
            "name": "主服务（改）",
            "base_url": "https://example.test/v1",
            "model": "demo-model-2",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["model"] == "demo-model-2"
    assert FAKE_KEY not in updated.text

    deleted = client.delete(f"/api/settings/api-profiles/{profile_id}")
    assert deleted.status_code == 204
    assert client.get("/api/settings/api-profiles").json()["items"] == []


def test_duplicate_profile_name_returns_400(client: TestClient) -> None:
    body = {"name": "同名", "base_url": "https://a.test/v1", "model": "m"}
    assert client.post("/api/settings/api-profiles", json=body).status_code == 201
    duplicate = client.post("/api/settings/api-profiles", json=body)
    assert duplicate.status_code == 400
    assert "已存在" in duplicate.json()["detail"]


def test_missing_profile_returns_404(client: TestClient) -> None:
    assert client.get("/api/settings/api-profiles/424242").status_code == 404


def test_connection_test_endpoint_classifies_failure(
    client: TestClient, monkeypatch
) -> None:
    from babeldoc_workbench.services import api_profiles as profiles_service

    created = client.post(
        "/api/settings/api-profiles",
        json={
            "name": "连接测试项",
            "base_url": "https://example.test/v1",
            "model": "demo",
            "api_key": FAKE_KEY,
        },
    ).json()

    monkeypatch.setattr(
        profiles_service,
        "test_connection",
        lambda **kwargs: {
            "ok": False,
            "category": "unreachable",
            "message": "地址不可达：请检查 Base URL、网络与代理设置",
            "detail": f"APIConnectionError: connect to {kwargs['base_url']}",
            "model": kwargs["model"],
        },
    )
    response = client.post(f"/api/settings/api-profiles/{created['id']}/test")
    assert response.status_code == 200
    assert response.json()["category"] == "unreachable"
    assert FAKE_KEY not in response.text


def test_params_schema_and_validation_endpoints(client: TestClient) -> None:
    schema = client.get("/api/settings/params/schema").json()
    keys = {item["key"] for item in schema["items"]}
    assert "lang_in" in keys
    assert "table_model" not in keys
    assert schema["defaults"]["qps"] == 4

    bad = client.post(
        "/api/settings/params/validate",
        json={"params": {"pages": "5-2", "no_mono": True, "no_dual": True}},
    ).json()
    assert bad["ok"] is False
    assert "pages" in bad["errors"] and "no_dual" in bad["errors"]

    good = client.post(
        "/api/settings/params/validate",
        json={"params": {"lang_in": "en", "lang_out": "zh", "pages": "1-3"}, "page_count": 5},
    ).json()
    assert good["ok"] is True
    assert good["engine_fields"]["lang_in"] == "en"


def test_preset_endpoints(client: TestClient) -> None:
    created = client.post(
        "/api/settings/presets", json={"name": "常用", "params": {"qps": 5}}
    )
    assert created.status_code == 201
    preset_id = created.json()["id"]
    assert client.get("/api/settings/presets").json()["items"][0]["name"] == "常用"
    assert client.get(f"/api/settings/presets/{preset_id}").json()["params"]["qps"] == 5
    assert client.delete(f"/api/settings/presets/{preset_id}").status_code == 204
    assert client.get("/api/settings/presets").json()["items"] == []
