"""术语表接口的端到端测试（走完整鉴权中间件）。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from babeldoc_workbench import security
from babeldoc_workbench.app import SessionInfo, create_app
from babeldoc_workbench.engine.selfcheck import run_light_checks

PORT = 8555
TOKEN = "fake-glossary-token"

CSV_BASIC = "source,target,tgt_lng\n神经网络,neural network,zh\n模型,model,zh\n"
CSV_CONFLICT = "source,target,tgt_lng\n模型,模型对象,zh\n"


@pytest.fixture
def client(workbench_db):
    session = SessionInfo(token=TOKEN, port=PORT)
    app = create_app(
        lambda deep=False: run_light_checks(), session, lambda: run_light_checks()
    )
    with TestClient(
        app, base_url=f"http://127.0.0.1:{PORT}", headers={security.TOKEN_HEADER: TOKEN}
    ) as test_client:
        yield test_client


def import_csv(
    client: TestClient, text: str = CSV_BASIC, name: str = "论文术语"
) -> dict:
    return client.post(
        "/api/glossaries/import",
        files={"file": ("terms.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
        data={"name": name, "tgt_lng": "zh"},
    ).json()


def test_import_and_list(client: TestClient) -> None:
    created = import_csv(client)
    assert created["entry_count"] == 2
    listed = client.get("/api/glossaries").json()["items"]
    assert listed[0]["name"] == "论文术语"
    assert listed[0]["conflict_count"] == 0


def test_import_rejects_bad_columns(client: TestClient) -> None:
    response = client.post(
        "/api/glossaries/import",
        files={"file": ("bad.csv", io.BytesIO(b"term,translation\n"), "text/csv")},
        data={"name": "坏文件", "tgt_lng": "zh"},
    )
    assert response.status_code == 400
    assert "source" in response.json()["detail"]


def test_entries_filters_and_edit(client: TestClient) -> None:
    glossary = import_csv(client)
    entries = client.get(f"/api/glossaries/{glossary['id']}/entries").json()
    assert entries["total"] == 2

    target = next(item for item in entries["items"] if item["source"] == "模型")
    updated = client.patch(
        f"/api/glossaries/entries/{target['id']}",
        json={"target": "AI 模型"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "edited"

    approved = client.post(
        f"/api/glossaries/{glossary['id']}/entries/bulk-status",
        json={"entry_ids": [target["id"]], "status": "approved"},
    )
    assert approved.json()["updated"] == 1

    filtered = client.get(
        f"/api/glossaries/{glossary['id']}/entries", params={"status": "approved"}
    ).json()
    assert filtered["total"] == 1
    searched = client.get(
        f"/api/glossaries/{glossary['id']}/entries", params={"q": "神经"}
    ).json()
    assert searched["items"][0]["source"] == "神经网络"


def test_conflict_flow_and_version_creation(client: TestClient) -> None:
    glossary = import_csv(client)
    # 再次导入同源词不同译词 → 冲突
    client.post(
        "/api/glossaries/import",
        files={"file": ("terms2.csv", io.BytesIO(CSV_CONFLICT.encode()), "text/csv")},
        data={"name": "另一份", "tgt_lng": "zh"},
    )
    entries = client.get(f"/api/glossaries/{glossary['id']}/entries").json()
    # 冲突发生在同一术语表内：手动导入冲突行到原表
    from babeldoc_workbench.services import glossaries

    glossaries.import_rows(
        glossaries.get_glossary(glossary["id"]), glossaries.parse_csv(CSV_CONFLICT.encode())
    )
    blocked = client.post(f"/api/glossaries/{glossary['id']}/versions", json={})
    assert blocked.status_code == 400
    assert "冲突" in blocked.json()["detail"]

    entries = client.get(
        f"/api/glossaries/{glossary['id']}/entries", params={"status": "conflict"}
    ).json()["items"]
    assert len(entries) == 1
    resolved = client.post(
        f"/api/glossaries/{glossary['id']}/resolve-conflict",
        json={"entry_id": entries[0]["id"], "keep": "incoming"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["target"] == "模型对象"

    version = client.post(f"/api/glossaries/{glossary['id']}/versions", json={"note": "审核通过"})
    assert version.status_code == 201
    assert version.json()["version"] == 1
    assert Path(version.json()["snapshot_path"]).is_file()

    versions = client.get(f"/api/glossaries/{glossary['id']}/versions").json()["items"]
    assert versions[0]["note"] == "审核通过"


def test_export_returns_csv_attachment(client: TestClient) -> None:
    glossary = import_csv(client)
    response = client.get(f"/api/glossaries/{glossary['id']}/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    body = response.content.decode("utf-8-sig")
    assert body.startswith("source,target,tgt_lng")
    assert "神经网络,neural network,zh" in body


def test_versions_endpoint_filters_by_language(client: TestClient) -> None:
    glossary = import_csv(client)
    client.post(f"/api/glossaries/{glossary['id']}/versions", json={})
    zh_versions = client.get("/api/glossaries/versions", params={"tgt_lng": "zh"}).json()
    ja_versions = client.get("/api/glossaries/versions", params={"tgt_lng": "ja"}).json()
    assert zh_versions["items"][0]["glossary_name"] == "论文术语"
    assert ja_versions["items"] == []


def test_missing_glossary_returns_404(client: TestClient) -> None:
    assert client.get("/api/glossaries/999/entries").status_code == 404
    assert client.patch("/api/glossaries/entries/999", json={"target": "x"}).status_code == 404
