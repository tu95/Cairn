from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import yaml

from cairn.server import db
from cairn.server.app import app
from cairn.server.routers import worker_config


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(tmp_path / "cairn.db")
    with TestClient(app) as test_client:
        yield test_client


def _create_project(client: TestClient) -> str:
    response = client.post(
        "/projects",
        json={
            "title": "test",
            "origin": "starting point",
            "goal": "finish",
            "hints": [{"content": "initial clue", "creator": "human"}],
        },
    )
    assert response.status_code == 201
    assert response.json()["project"]["bootstrap_enabled"] is True
    return response.json()["project"]["id"]


def test_worker_config_writes_codex_local_auth_without_api_key(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    config_path = tmp_path / "dispatch.yaml"
    monkeypatch.setattr(worker_config, "CONFIG_PATH", config_path)

    response = client.put(
        "/worker-config",
        json={
            "provider": "codex",
            "auth_mode": "local",
            "model": "",
            "base_url": "",
            "api_key": "",
            "max_running": 2,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["auth_mode"] == "local"
    assert data["api_key"] == ""

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    env = config["workers"][0]["env"]
    assert env == {"CODEX_AUTH_MODE": "local"}
    assert config["workers"][0]["max_running"] == 2


def test_worker_config_pings_codex_local_auth(client: TestClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(worker_config, "CONFIG_PATH", tmp_path / "dispatch.yaml")

    def fake_run(*args, **kwargs):
        assert args[0] == [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--",
            "Reply with exactly: pong",
        ]
        return SimpleNamespace(returncode=0, stdout="pong\n", stderr="")

    monkeypatch.setattr(worker_config.subprocess, "run", fake_run)

    response = client.post(
        "/worker-config/ping",
        json={
            "provider": "codex",
            "auth_mode": "local",
            "model": "",
            "base_url": "",
            "api_key": "",
            "max_running": 1,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status_code": 0, "preview": "pong\n"}


def test_worker_models_support_add_remove_and_default(client: TestClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(worker_config, "CONFIG_PATH", tmp_path / "dispatch.yaml")

    response = client.get("/worker-models?provider=codex")
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "codex"
    assert payload["items"]
    assert payload["default"] == payload["items"][0]

    response = client.put(
        "/worker-models",
        json={"provider": "codex", "items": ["gpt-5.1-codex", "gpt-5-mini"], "default": "gpt-5-mini"},
    )
    assert response.status_code == 200
    assert response.json()["default"] == "gpt-5-mini"

    response = client.put(
        "/worker-models",
        json={"provider": "codex", "items": ["gpt-5-mini"], "default": "gpt-5-mini"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == ["gpt-5-mini"]

    response = client.get("/worker-models?provider=codex")
    assert response.status_code == 200
    assert response.json()["items"] == ["gpt-5-mini"]


def test_worker_config_save_triggers_live_reload_without_restart_notice(client: TestClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(worker_config, "CONFIG_PATH", tmp_path / "dispatch.yaml")
    called = {"value": False}

    def mark_reload() -> None:
        called["value"] = True

    worker_config.set_dispatcher_reload_callback(mark_reload)
    try:
        response = client.put(
            "/worker-config",
            json={
                "provider": "codex",
                "auth_mode": "local",
                "model": "",
                "base_url": "",
                "api_key": "",
                "max_running": 2,
            },
        )
    finally:
        worker_config.set_dispatcher_reload_callback(None)

    assert response.status_code == 200
    assert response.json()["restart_required"] is False
    assert called["value"] is True


def test_project_workflow_create_conclude_complete_and_reopen(client: TestClient) -> None:
    project_id = _create_project(client)

    response = client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "investigate", "creator": "reasoner", "worker": None},
    )
    assert response.status_code == 201
    assert response.json()["id"] == "i001"

    response = client.post(
        f"/projects/{project_id}/intents/i001/heartbeat",
        json={"worker": "explorer"},
    )
    assert response.status_code == 200
    assert response.json()["worker"] == "explorer"

    response = client.post(
        f"/projects/{project_id}/intents/i001/conclude",
        json={"worker": "explorer", "description": "new fact"},
    )
    assert response.status_code == 200
    assert response.json()["fact"] == {"id": "f001", "description": "new fact"}

    response = client.post(
        f"/projects/{project_id}/complete",
        json={"from": ["f001"], "description": "solved", "worker": "reasoner"},
    )
    assert response.status_code == 200
    assert response.json()["to"] == "goal"

    response = client.post(
        f"/projects/{project_id}/reopen",
        json={"description": "human correction", "creator": "human"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"]["status"] == "active"
    assert payload["fact"] == {"id": "f002", "description": "human correction"}
    assert payload["intent"]["from"] == ["f001"]
    assert payload["intent"]["to"] == "f002"


def test_stopping_project_releases_claims_and_reason_but_keeps_hints_writable(client: TestClient) -> None:
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "work", "creator": "worker-a", "worker": "worker-a"},
    )
    client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-b", "trigger": "facts:2->3"},
    )

    response = client.put(f"/projects/{project_id}/status", json={"status": "stopped"})
    assert response.status_code == 200
    assert response.json()["reason"] is None

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["intents"][0]["worker"] is None
    assert client.post(
        f"/projects/{project_id}/hints",
        json={"content": "manual note", "creator": "human"},
    ).status_code == 201
    assert client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "blocked", "creator": "reasoner", "worker": None},
    ).status_code == 403


def test_intent_creation_rejects_goal_source_and_mismatched_initial_worker(client: TestClient) -> None:
    project_id = _create_project(client)

    assert client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["goal"], "description": "invalid", "creator": "reasoner", "worker": None},
    ).status_code == 400
    assert client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "invalid", "creator": "reasoner", "worker": "explorer"},
    ).status_code == 400


def test_settings_and_export_are_backed_by_the_same_database(client: TestClient) -> None:
    project_id = _create_project(client)

    response = client.put("/settings", json={"intent_timeout": 30, "reason_timeout": 45})
    assert response.status_code == 200
    assert client.get("/settings").json() == {"intent_timeout": 30, "reason_timeout": 45}

    exported = client.get(f"/projects/{project_id}/export?format=yaml")
    assert exported.status_code == 200
    assert "origin: starting point" in exported.text
    assert "goal: finish" in exported.text
    assert client.get(f"/projects/{project_id}/export?format=invalid").status_code == 400


def test_expired_intent_and_reason_leases_can_be_reclaimed(client: TestClient) -> None:
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "work", "creator": "worker-a", "worker": "worker-a"},
    )
    client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-a", "trigger": "bootstrap"},
    )
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE intents SET last_heartbeat_at = '2000-01-01T00:00:00Z' WHERE project_id = ?",
            (project_id,),
        )
        conn.execute(
            "UPDATE projects SET reason_last_heartbeat_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (project_id,),
        )

    response = client.post(
        f"/projects/{project_id}/intents/i001/heartbeat",
        json={"worker": "worker-b"},
    )
    assert response.status_code == 200
    assert response.json()["worker"] == "worker-b"

    response = client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-b", "trigger": "facts:2->3"},
    )
    assert response.status_code == 200
    assert response.json()["reason"]["worker"] == "worker-b"


def test_live_reason_lease_rejects_competing_worker(client: TestClient) -> None:
    project_id = _create_project(client)
    assert client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-a", "trigger": "bootstrap"},
    ).status_code == 200

    response = client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-b", "trigger": "facts:2->3"},
    )

    assert response.status_code == 409
    assert "worker-a" in response.json()["detail"]


def test_project_creation_persists_disabled_bootstrap_and_exports_it(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "no bootstrap",
            "origin": "start",
            "goal": "finish",
            "bootstrap_enabled": False,
        },
    )

    assert response.status_code == 201
    project_id = response.json()["project"]["id"]
    assert client.get(f"/projects/{project_id}").json()["project"]["bootstrap_enabled"] is False
    assert "bootstrap_enabled: false" in client.get(f"/projects/{project_id}/export?format=yaml").text


def test_project_creation_rejects_invalid_bootstrap_enabled(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "invalid bootstrap",
            "origin": "start",
            "goal": "finish",
            "bootstrap_enabled": "sometimes",
        },
    )

    assert response.status_code == 422
