"""任务与文件接口的端到端测试（走完整鉴权中间件；工作进程用替身）。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from babeldoc_workbench import security
from babeldoc_workbench.api import tasks as tasks_api
from babeldoc_workbench.app import SessionInfo, bind_loopback_socket, create_app, start_server
from babeldoc_workbench.engine.selfcheck import run_light_checks
from babeldoc_workbench.services import api_profiles, task_store
from babeldoc_workbench.services.task_queue import QueueConfig, TaskQueue
from engine_fakes import make_fake_runner

FAKE_KEY = "sk-fake-tasks-api-key"
PORT = 8321
TOKEN = "fake-tasks-api-token"


def make_sample_pdf(path: Path, pages: int = 2) -> Path:
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 96), f"Sample page {index + 1}", fontsize=16)
    document.save(path)
    document.close()
    return path


def build_queue(runner) -> TaskQueue:
    return TaskQueue(
        runner=runner,
        config=QueueConfig(poll_interval=0.05, db_event_interval=0.05, sse_event_interval=0.0),
    )


@pytest.fixture
def queue(workbench_db):
    task_queue = build_queue(make_fake_runner(step_delay=0.02))
    task_queue.start()
    tasks_api.set_queue(task_queue)
    try:
        yield task_queue
    finally:
        tasks_api.set_queue(None)
        task_queue.stop()


@pytest.fixture
def client(workbench_db, fake_keyring, queue):
    session = SessionInfo(token=TOKEN, port=PORT)
    app = create_app(
        lambda deep=False: run_light_checks(), session, lambda: run_light_checks()
    )
    with TestClient(
        app, base_url=f"http://127.0.0.1:{PORT}", headers={security.TOKEN_HEADER: TOKEN}
    ) as test_client:
        yield test_client


@pytest.fixture
def profile(workbench_db, fake_keyring):
    return api_profiles.create_profile(
        name="任务测试",
        base_url="https://example.test/v1",
        model="demo",
        api_key=FAKE_KEY,
    )


def import_pdf(client: TestClient, tmp_path: Path, name: str = "sample.pdf") -> dict:
    pdf = make_sample_pdf(tmp_path / name)
    with pdf.open("rb") as handle:
        response = client.post(
            "/api/files/import", files={"files": (name, handle, "application/pdf")}
        )
    assert response.status_code == 201, response.text
    return response.json()["items"][0]


def wait_for_terminal(client: TestClient, task_id: int, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/tasks/{task_id}").json()
        if payload["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
            return payload
        time.sleep(0.05)
    return client.get(f"/api/tasks/{task_id}").json()


def test_import_rejects_non_pdf(client: TestClient, tmp_path: Path) -> None:
    txt = tmp_path / "note.txt"
    txt.write_text("hello", encoding="utf-8")
    with txt.open("rb") as handle:
        response = client.post(
            "/api/files/import", files={"files": ("note.txt", handle, "text/plain")}
        )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_import_and_list_files(client: TestClient, tmp_path: Path) -> None:
    staged = import_pdf(client, tmp_path)
    assert staged["name"] == "sample.pdf"
    assert staged["size"] > 0
    listed = client.get("/api/files").json()["items"]
    assert [item["id"] for item in listed] == [staged["id"]]


def test_create_task_runs_to_success(client: TestClient, tmp_path: Path, profile) -> None:
    staged = import_pdf(client, tmp_path)
    response = client.post(
        "/api/tasks",
        json={
            "file_ids": [staged["id"]],
            "api_profile_id": profile["id"],
            "params": {"lang_in": "en", "lang_out": "zh", "qps": 6},
        },
    )
    assert response.status_code == 201, response.text
    task = response.json()["items"][0]
    assert task["params"]["qps"] == 6
    assert task["status"] in {"queued", "preparing", "running"}

    finished = wait_for_terminal(client, task["id"])
    assert finished["status"] == "succeeded"
    assert finished["progress"] == 100.0
    kinds = {item["kind"] for item in finished["outputs"]}
    assert {"mono", "dual"} <= kinds
    assert finished["events"], "任务事件应当在详情里可见"

    listed = client.get("/api/tasks").json()["items"]
    assert any(item["id"] == task["id"] for item in listed)


def test_output_dirs_export_results_without_overwriting(
    client: TestClient, queue: TaskQueue, tmp_path: Path, profile, monkeypatch
) -> None:
    from babeldoc_workbench.services import lifecycle

    first_dirs = {kind: tmp_path / f"用户成果-{kind}" for kind in ("mono", "dual", "glossary")}
    second_dirs = {kind: tmp_path / f"后来修改的目录-{kind}" for kind in first_dirs}
    for path in (*first_dirs.values(), *second_dirs.values()):
        path.mkdir()
    assert client.put("/api/settings/app", json={
        f"default_{kind}_output_dir": str(path) for kind, path in first_dirs.items()
    }).status_code == 200

    queue.stop()
    staged = import_pdf(client, tmp_path)
    first = client.post(
        "/api/tasks",
        json={"file_ids": [staged["id"]], "api_profile_id": profile["id"], "params": {}},
    ).json()["items"][0]
    assert first["params"]["_user_output_dirs"] == {
        kind: str(path) for kind, path in first_dirs.items()
    }
    assert client.put("/api/settings/app", json={
        f"default_{kind}_output_dir": str(path) for kind, path in second_dirs.items()
    }).status_code == 200
    queue.start()

    finished = wait_for_terminal(client, first["id"])
    assert finished["status"] == "succeeded"
    exported = next(event["payload"] for event in finished["events"] if event["type"] == "exported")
    assert exported["errors"] == []
    assert {item["kind"] for item in exported["items"]} == {"mono", "dual", "glossary"}
    assert all(Path(item["path"]).parent == first_dirs[item["kind"]] for item in exported["items"])
    assert all(Path(item["path"]).is_file() for item in exported["items"])
    assert all(not list(path.iterdir()) for path in second_dirs.values()), "运行前改设置不应改变已提交任务的输出目录"
    assert all(Path(item["path"]).is_file() for item in finished["outputs"])
    revealed: list[Path] = []
    monkeypatch.setattr(lifecycle, "reveal_in_explorer", lambda path: revealed.append(path))
    for kind, directory in first_dirs.items():
        shown = client.post(f"/api/tasks/{first['id']}/outputs/{kind}/reveal")
        assert shown.status_code == 200
        assert Path(shown.json()["path"]).parent == directory
    assert revealed == list(first_dirs.values())
    manual = client.post(
        f"/api/tasks/{first['id']}/outputs/mono/save-as",
        json={"target_dir": str(second_dirs["mono"])},
    )
    assert manual.status_code == 200
    assert Path(manual.json()["target"]).parent == second_dirs["mono"]
    mono_source = next(Path(item["path"]) for item in finished["outputs"] if item["kind"] == "mono")
    mono_source.unlink()
    shown_after_cleanup = client.post(f"/api/tasks/{first['id']}/outputs/mono/reveal")
    assert shown_after_cleanup.status_code == 200
    assert Path(shown_after_cleanup.json()["path"]).parent == first_dirs["mono"]

    assert client.put("/api/settings/app", json={
        f"default_{kind}_output_dir": str(path) for kind, path in first_dirs.items()
    }).status_code == 200
    second = create_finished_task(client, tmp_path, profile)
    second_export = next(event["payload"] for event in second["events"] if event["type"] == "exported")
    assert second_export["errors"] == []
    assert all("任务" in Path(item["path"]).name for item in second_export["items"])
    assert {item["path"] for item in exported["items"]}.isdisjoint(
        item["path"] for item in second_export["items"]
    )
    assert all(Path(item["path"]).is_file() for item in exported["items"])


def test_export_failure_keeps_managed_outputs_and_success_status(
    client: TestClient, queue: TaskQueue, tmp_path: Path, profile, monkeypatch
) -> None:
    from babeldoc_workbench.services import lifecycle

    target = tmp_path / "随后被删除的目录"
    retained = tmp_path / "仍存在的目录"
    target.mkdir()
    retained.mkdir()
    client.put("/api/settings/app", json={
        "default_mono_output_dir": str(target),
        "default_dual_output_dir": str(retained),
    })
    queue.stop()
    staged = import_pdf(client, tmp_path)
    task = client.post(
        "/api/tasks",
        json={"file_ids": [staged["id"]], "api_profile_id": profile["id"], "params": {}},
    ).json()["items"][0]
    target.rmdir()
    queue.start()

    finished = wait_for_terminal(client, task["id"])
    assert finished["status"] == "succeeded"
    exported = next(event["payload"] for event in finished["events"] if event["type"] == "exported")
    assert {item["kind"] for item in exported["items"]} == {"dual"}
    assert exported["errors"]
    assert Path(exported["items"][0]["path"]).parent == retained
    assert all(Path(item["path"]).is_file() for item in finished["outputs"])
    shown: list[Path] = []
    monkeypatch.setattr(lifecycle, "reveal_in_explorer", lambda path: shown.append(path))
    response = client.post(f"/api/tasks/{task['id']}/outputs/mono/reveal")
    assert response.status_code == 200
    assert Path(response.json()["path"]).parent == Path(finished["output_dir"])
    assert shown == [Path(finished["output_dir"])]


def test_legacy_output_dir_can_be_cleared_for_one_kind(
    client: TestClient, tmp_path: Path, profile
) -> None:
    legacy = tmp_path / "旧默认目录"
    legacy.mkdir()
    response = client.put("/api/settings/app", json={"default_output_dir": str(legacy)})
    assert response.status_code == 200
    assert response.json()["default_mono_output_dir"] == str(legacy)
    assert response.json()["default_dual_output_dir"] == str(legacy)
    assert response.json()["default_glossary_output_dir"] == str(legacy)
    response = client.put("/api/settings/app", json={"default_glossary_output_dir": ""})
    assert response.status_code == 200
    assert response.json()["default_glossary_output_dir"] == ""

    finished = create_finished_task(client, tmp_path, profile)
    assert finished["params"]["_user_output_dirs"] == {
        "mono": str(legacy), "dual": str(legacy),
    }
    exported = next(event["payload"] for event in finished["events"] if event["type"] == "exported")
    assert {item["kind"] for item in exported["items"]} == {"mono", "dual"}
    assert exported["errors"] == []
    assert all(Path(item["path"]).is_file() for item in finished["outputs"])


def test_create_task_rejects_invalid_params(client: TestClient, tmp_path: Path, profile) -> None:
    staged = import_pdf(client, tmp_path)
    response = client.post(
        "/api/tasks",
        json={
            "file_ids": [staged["id"]],
            "api_profile_id": profile["id"],
            "params": {"pages": "9-2", "qps": 0},
        },
    )
    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert "pages" in errors and "qps" in errors


def test_create_task_requires_api_profile(client: TestClient, tmp_path: Path) -> None:
    staged = import_pdf(client, tmp_path)
    response = client.post("/api/tasks", json={"file_ids": [staged["id"]], "params": {}})
    assert response.status_code == 400
    assert "API 配置" in response.json()["detail"]


def test_create_task_validates_page_range_against_pdf(
    client: TestClient, tmp_path: Path, profile
) -> None:
    staged = import_pdf(client, tmp_path)  # 2 页
    response = client.post(
        "/api/tasks",
        json={
            "file_ids": [staged["id"]],
            "api_profile_id": profile["id"],
            "params": {"pages": "1-5"},
        },
    )
    assert response.status_code == 400
    assert "共 2 页" in response.json()["detail"]["errors"]["pages"]


def test_rerun_creates_new_task(client: TestClient, tmp_path: Path, profile) -> None:
    staged = import_pdf(client, tmp_path)
    created = client.post(
        "/api/tasks",
        json={"file_ids": [staged["id"]], "api_profile_id": profile["id"], "params": {}},
    ).json()["items"][0]
    finished = wait_for_terminal(client, created["id"])
    assert finished["status"] == "succeeded"

    rerun = client.post(f"/api/tasks/{created['id']}/rerun")
    assert rerun.status_code == 201
    new_task = rerun.json()
    assert new_task["id"] != created["id"]
    assert new_task["input_name"] == finished["input_name"]

    original = client.get(f"/api/tasks/{created['id']}").json()
    assert original["status"] == "succeeded"
    wait_for_terminal(client, new_task["id"])


def test_delete_running_task_is_rejected(
    client: TestClient, tmp_path: Path, profile, queue
) -> None:
    queue.runner = make_fake_runner(step_delay=0.2, wait_for_cancel=True)
    staged = import_pdf(client, tmp_path)
    task = client.post(
        "/api/tasks",
        json={"file_ids": [staged["id"]], "api_profile_id": profile["id"], "params": {}},
    ).json()["items"][0]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if client.get(f"/api/tasks/{task['id']}").json()["status"] == "running":
            break
        time.sleep(0.05)
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 409
    assert client.post(f"/api/tasks/{task['id']}/cancel").status_code == 200
    wait_for_terminal(client, task["id"])
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404


def test_sse_snapshot_for_finished_task(client: TestClient, tmp_path: Path, profile) -> None:
    staged = import_pdf(client, tmp_path)
    task = client.post(
        "/api/tasks",
        json={"file_ids": [staged["id"]], "api_profile_id": profile["id"], "params": {}},
    ).json()["items"][0]
    wait_for_terminal(client, task["id"])

    with client.stream("GET", f"/api/tasks/{task['id']}/events") as response:
        assert response.status_code == 200
        body = "".join(response.iter_lines())
    assert "event: snapshot" in body
    assert "succeeded" in body


def test_sse_live_stream_and_cancel(workbench_db, fake_keyring, tmp_path: Path, profile) -> None:
    import threading

    queue = build_queue(make_fake_runner(block_until_cancel=True))
    queue.start()
    tasks_api.set_queue(queue)
    session = SessionInfo(token=TOKEN)
    sock, port = bind_loopback_socket()
    session.port = port
    app = create_app(
        lambda deep=False: run_light_checks(), session, lambda: run_light_checks()
    )
    server = start_server(app, sock)
    base = f"http://127.0.0.1:{port}"
    headers = {security.TOKEN_HEADER: TOKEN}
    try:
        pdf = make_sample_pdf(tmp_path / "live.pdf")
        with pdf.open("rb") as handle:
            staged = httpx.post(
                f"{base}/api/files/import",
                files={"files": ("live.pdf", handle, "application/pdf")},
                headers=headers,
                timeout=30,
            ).json()["items"][0]
        task = httpx.post(
            f"{base}/api/tasks",
            json={
                "file_ids": [staged["id"]],
                "api_profile_id": profile["id"],
                "params": {},
            },
            headers=headers,
            timeout=30,
        ).json()["items"][0]

        seen_events: list[str] = []

        def read_stream() -> None:
            try:
                with httpx.stream(
                    "GET",
                    f"{base}/api/tasks/{task['id']}/events",
                    headers=headers,
                    timeout=30,
                ) as response:
                    for line in response.iter_lines():
                        if line.startswith("event:"):
                            seen_events.append(line.split(":", 1)[1].strip())
            except Exception as exc:  # pragma: no cover - 仅用于诊断
                seen_events.append(f"stream-error:{type(exc).__name__}")

        stream_thread = threading.Thread(target=read_stream, daemon=True)
        stream_thread.start()
        time.sleep(1.0)

        cancelled = httpx.post(
            f"{base}/api/tasks/{task['id']}/cancel", headers=headers, timeout=30
        )
        assert cancelled.status_code == 200
        stream_thread.join(30)
        assert "snapshot" in seen_events, seen_events
        assert "cancelled" in seen_events, seen_events
        deadline = time.monotonic() + 20
        status = ""
        while time.monotonic() < deadline:
            status = httpx.get(
                f"{base}/api/tasks/{task['id']}", headers=headers, timeout=30
            ).json()["status"]
            if status in {"cancelled", "failed", "succeeded"}:
                break
            time.sleep(0.1)
        assert status == "cancelled"
        assert queue.current_task_id() is None or status == "cancelled"
    finally:
        server.should_exit = True
        tasks_api.set_queue(None)
        queue.stop()
        # 服务线程退出后再结束测试
        time.sleep(0.5)


def create_finished_task(client: TestClient, tmp_path: Path, profile) -> dict:
    staged = import_pdf(client, tmp_path)
    task = client.post(
        "/api/tasks",
        json={"file_ids": [staged["id"]], "api_profile_id": profile["id"], "params": {}},
    ).json()["items"][0]
    finished = wait_for_terminal(client, task["id"])
    assert finished["status"] == "succeeded"
    return finished


def test_task_files_and_cleanup_keep_inputs(client: TestClient, tmp_path: Path, profile) -> None:
    finished = create_finished_task(client, tmp_path, profile)
    described = client.get(f"/api/tasks/{finished['id']}/files").json()
    by_name = {item["name"]: item for item in described["items"]}
    assert by_name["input"]["size"] > 0
    assert by_name["output"]["size"] > 0

    cleanup = client.post(f"/api/tasks/{finished['id']}/cleanup")
    assert cleanup.status_code == 200
    assert cleanup.json()["removed"]
    after = client.get(f"/api/tasks/{finished['id']}/files").json()
    after_by_name = {item["name"]: item for item in after["items"]}
    assert after_by_name["work"]["exists"] is False
    assert after_by_name["input"]["exists"] is True, "清理临时文件不得删除输入副本"
    assert after_by_name["output"]["exists"] is True, "清理临时文件不得删除成果"


def test_output_actions_and_delete_semantics(
    client: TestClient, tmp_path: Path, profile, monkeypatch
) -> None:
    from babeldoc_workbench.services import lifecycle

    finished = create_finished_task(client, tmp_path, profile)
    opened: list[Path] = []
    monkeypatch.setattr(lifecycle, "open_with_default_app", lambda path: opened.append(path))
    monkeypatch.setattr(lifecycle, "reveal_in_explorer", lambda path: opened.append(path))

    assert client.post(f"/api/tasks/{finished['id']}/reveal").status_code == 200
    opened_result = client.post(f"/api/tasks/{finished['id']}/outputs/mono/open")
    assert opened_result.status_code == 200
    assert len(opened) == 2

    wrong_kind = client.post(f"/api/tasks/{finished['id']}/outputs/log/open")
    assert wrong_kind.status_code == 400

    target = tmp_path / "另存位置"
    target.mkdir()
    saved = client.post(
        f"/api/tasks/{finished['id']}/outputs/mono/save-as",
        json={"target_dir": str(target)},
    )
    assert saved.status_code == 200
    saved_path = Path(saved.json()["target"])
    assert saved_path.is_file() and saved_path.parent == target

    output_dir = Path(finished["output_dir"])
    assert output_dir.is_dir()
    # 只删除记录：文件保留
    assert client.delete(f"/api/tasks/{finished['id']}").status_code == 204
    assert output_dir.exists(), "删除记录不得删除文件"


def test_delete_record_with_files_removes_task_directory(
    client: TestClient, tmp_path: Path, profile
) -> None:
    finished = create_finished_task(client, tmp_path, profile)
    output_dir = Path(finished["output_dir"])
    task_dir = output_dir.parent
    assert client.delete(f"/api/tasks/{finished['id']}?delete_files=true").status_code == 204
    # 计划书的实现只删除 input/work/output/logs 四个子目录，保留任务根目录
    for name in ("input", "work", "output", "logs"):
        assert not (task_dir / name).exists(), f"{name} 应已删除"
    assert list(task_dir.iterdir()) == [], "任务目录应为空"


def test_app_settings_endpoints(client: TestClient, tmp_path: Path) -> None:
    defaults = client.get("/api/settings/app").json()
    assert defaults["log_level"] == "INFO"

    target = tmp_path / "默认产出"
    target.mkdir()
    updated = client.put(
        "/api/settings/app",
        json={
            "default_output_dir": str(target),
            "retention_days": 7,
            "log_level": "WARNING",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["retention_days"] == 7

    invalid = client.put("/api/settings/app", json={"log_level": "TRACE"})
    assert invalid.status_code == 400
