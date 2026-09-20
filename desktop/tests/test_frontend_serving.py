"""后端挂载前端产物的测试（不需要 node_modules）。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from babeldoc_workbench import security
from babeldoc_workbench.app import SessionInfo, create_app
from babeldoc_workbench.engine.selfcheck import run_light_checks

PORT = 8422
TOKEN = "fake-frontend-token"


def make_client(frontend_dir: Path | None = None) -> TestClient:
    session = SessionInfo(token=TOKEN, port=PORT)
    app = create_app(
        lambda deep=False: run_light_checks(),
        session,
        lambda: run_light_checks(),
        frontend_dir=frontend_dir,
    )
    return TestClient(
        app, base_url=f"http://127.0.0.1:{PORT}", headers={security.TOKEN_HEADER: TOKEN}
    )


def test_root_falls_back_to_self_check_page(workbench_db, tmp_path: Path) -> None:
    with make_client(tmp_path / "missing-dist") as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "环境自检" in response.text


def test_built_frontend_is_served(workbench_db, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>工作台</title><div id=\"root\">BabelDOC 工作台</div>",
        encoding="utf-8",
    )
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")

    with make_client(dist) as client:
        index = client.get("/")
        asset = client.get("/assets/app.js")  # 静态资源免令牌
        selfcheck = client.get("/selfcheck")
        api_without_token = client.get(
            "/api/settings/api-profiles", headers={security.TOKEN_HEADER: ""}
        )

    assert index.status_code == 200
    assert "BabelDOC 工作台" in index.text
    assert asset.status_code == 200
    assert "console.log" in asset.text
    assert selfcheck.status_code == 200 and "环境自检" in selfcheck.text
    assert api_without_token.status_code == 403
