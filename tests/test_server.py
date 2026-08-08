"""Tests for the HTTP surface: auth guard, settings masking, error mapping."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import providers  # noqa: E402
import server  # noqa: E402

HEADERS = {"X-AutoClassroom-Client": "test"}
EXT_ORIGIN = "chrome-extension://" + "a" * 32


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated config file per test so real settings are never touched."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DEFAULT_OUTPUT_DIR", str(tmp_path / "lab"))
    for _label, env_var, _m, _b in config.PROVIDERS.values():
        monkeypatch.delenv(env_var, raising=False)
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


def test_client_header_required(client):
    assert client.get("/health").status_code == 403
    assert client.post("/settings", json={}).status_code == 403
    assert client.get("/health", headers=HEADERS).status_code == 200


def test_cors_allows_extension_origin(client):
    resp = client.get("/health", headers={**HEADERS, "Origin": EXT_ORIGIN})
    assert resp.headers.get("Access-Control-Allow-Origin") == EXT_ORIGIN


def test_cors_rejects_web_origin(client):
    resp = client.get("/health", headers={**HEADERS, "Origin": "https://evil.example.com"})
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_preflight_rejects_web_origin(client):
    resp = client.options("/process_assignment", headers={
        "Origin": "https://evil.example.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-autoclassroom-client",
    })
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_settings_get_shape(client):
    body = client.get("/settings", headers=HEADERS).get_json()
    assert "output_dir" in body and "providers" in body
    keys = {p["key"] for p in body["providers"]}
    assert {"gemini", "openai", "anthropic", "openrouter", "groq", "xai", "custom"} <= keys


def test_settings_never_returns_raw_key(client):
    secret = "sk-super-secret-value-987654321"
    client.post("/settings", headers=HEADERS, json={"api_keys": {"openai": secret}})
    raw = client.get("/settings", headers=HEADERS).get_data(as_text=True)
    assert secret not in raw
    entry = [p for p in json.loads(raw)["providers"] if p["key"] == "openai"][0]
    assert entry["has_key"] is True
    assert entry["masked_key"] == "sk-s********4321"
    # ...but the server can still resolve it internally.
    assert config.get_api_key("openai") == secret


def test_settings_round_trip(client, tmp_path):
    out = tmp_path / "solutions"
    resp = client.post("/settings", headers=HEADERS, json={
        "output_dir": str(out), "provider": "groq",
        "models": {"groq": "llama-3.1-8b-instant"}, "run_code": True,
    })
    assert resp.status_code == 200
    body = resp.get_json()["settings"]
    assert body["provider"] == "groq"
    assert body["run_code"] is True
    assert os.path.isdir(out)
    groq = [p for p in body["providers"] if p["key"] == "groq"][0]
    assert groq["model"] == "llama-3.1-8b-instant"


def test_settings_rejects_unwritable_folder(client):
    resp = client.post("/settings", headers=HEADERS,
                       json={"output_dir": "Z:/definitely/not/mounted/xyz"})
    assert resp.status_code == 400
    assert "Cannot write" in resp.get_json()["error"]


def test_settings_rejects_non_object(client):
    assert client.post("/settings", headers=HEADERS, json=["a"]).status_code == 400


def test_settings_ignores_unknown_provider(client):
    client.post("/settings", headers=HEADERS, json={"provider": "hackerman"})
    assert client.get("/settings", headers=HEADERS).get_json()["provider"] == "gemini"


def test_clearing_key_falls_back_to_env(client, monkeypatch):
    client.post("/settings", headers=HEADERS, json={"api_keys": {"openai": "sk-stored"}})
    client.post("/settings", headers=HEADERS, json={"api_keys": {"openai": ""}})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    entry = [p for p in client.get("/settings", headers=HEADERS).get_json()["providers"]
             if p["key"] == "openai"][0]
    assert entry["has_key"] is True and entry["key_from_env"] is True
    assert entry["masked_key"] == ""


def test_test_provider_success(client, monkeypatch):
    monkeypatch.setattr(providers, "test", lambda p, cfg=None: "OK")
    body = client.post("/test_provider", headers=HEADERS, json={"provider": "openai"}).get_json()
    assert body["success"] is True and body["reply"] == "OK"


def test_test_provider_reports_failure_as_200(client, monkeypatch):
    def boom(p, cfg=None):
        raise providers.ProviderError("HTTP 401: bad key")

    monkeypatch.setattr(providers, "test", boom)
    resp = client.post("/test_provider", headers=HEADERS, json={"provider": "openai"})
    assert resp.status_code == 200
    assert resp.get_json()["success"] is False
    assert "401" in resp.get_json()["error"]


def test_test_provider_unknown(client):
    assert client.post("/test_provider", headers=HEADERS,
                       json={"provider": "nope"}).status_code == 400


def test_process_assignment_requires_ids(client):
    assert client.post("/process_assignment", headers=HEADERS, json={}).status_code == 400


def test_process_assignment_success(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "get_assignment_details",
                        lambda c, w: ("Lab 1", "Write bubble sort."))
    monkeypatch.setattr(server, "generate_solution",
                        lambda t, i, cfg=None: {"summary": "did it", "artifacts": [{}]})
    monkeypatch.setattr(server, "save_solution",
                        lambda t, s, cfg=None: {"dir": str(tmp_path), "files": ["main.py"],
                                                "notes": []})
    body = client.post("/process_assignment", headers=HEADERS,
                       json={"courseId": "1", "courseWorkId": "2"}).get_json()
    assert body["success"] is True
    assert body["files"] == ["main.py"]
    assert body["summary"] == "did it"


def test_provider_error_maps_to_502(client, monkeypatch):
    monkeypatch.setattr(server, "get_assignment_details", lambda c, w: ("L", "do it"))

    def boom(t, i, cfg=None):
        raise providers.ProviderError("No API key for OpenAI.")

    monkeypatch.setattr(server, "generate_solution", boom)
    resp = client.post("/process_assignment", headers=HEADERS,
                       json={"courseId": "1", "courseWorkId": "2"})
    assert resp.status_code == 502
    assert "No API key" in resp.get_json()["error"]


def test_value_error_maps_to_422(client, monkeypatch):
    monkeypatch.setattr(server, "get_assignment_details", lambda c, w: ("L", "do it"))

    def boom(t, i, cfg=None):
        raise ValueError("could not find valid JSON in the model's response")

    monkeypatch.setattr(server, "generate_solution", boom)
    resp = client.post("/process_assignment", headers=HEADERS,
                       json={"courseId": "1", "courseWorkId": "2"})
    assert resp.status_code == 422


def test_empty_instructions_rejected(client, monkeypatch):
    monkeypatch.setattr(server, "get_assignment_details", lambda c, w: ("Lab", "   "))
    resp = client.post("/process_assignment", headers=HEADERS,
                       json={"courseId": "1", "courseWorkId": "2"})
    assert resp.status_code == 400
    assert "no instructions" in resp.get_json()["error"]


def test_base64_ids_are_decoded(client, monkeypatch):
    import base64 as b64
    seen = {}

    def capture(course_id, coursework_id):
        seen["ids"] = (course_id, coursework_id)
        return ("Lab", "do it")

    monkeypatch.setattr(server, "get_assignment_details", capture)
    monkeypatch.setattr(server, "generate_solution",
                        lambda t, i, cfg=None: {"artifacts": [{}]})
    monkeypatch.setattr(server, "save_solution",
                        lambda t, s, cfg=None: {"dir": "d", "files": ["f"], "notes": []})
    encoded = b64.b64encode(b"123456").decode().rstrip("=")
    client.post("/process_assignment", headers=HEADERS,
                json={"courseId": encoded, "courseWorkId": encoded})
    assert seen["ids"] == ("123456", "123456")


def test_concurrent_run_is_rejected(client, monkeypatch):
    server._run_lock.acquire()
    try:
        resp = client.post("/process_assignment", headers=HEADERS,
                           json={"courseId": "1", "courseWorkId": "2"})
        assert resp.status_code == 409
    finally:
        server._run_lock.release()


def test_browse_folder_returns_picked_path(client, monkeypatch, tmp_path):
    class Proc:
        stdout, stderr = str(tmp_path), ""

    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: Proc())
    body = client.post("/browse_folder", headers=HEADERS, json={}).get_json()
    assert body["path"] == os.path.abspath(str(tmp_path))


def test_browse_folder_cancel(client, monkeypatch):
    class Proc:
        stdout, stderr = "", "cancelled"

    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: Proc())
    assert client.post("/browse_folder", headers=HEADERS, json={}).get_json()["cancelled"] is True


def test_auth_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(server, "check_auth_status", lambda: {"authenticated": True, "has_credentials": True, "message": "OK"})
    resp = client.get("/auth/status", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["authenticated"] is True


def test_auth_login_endpoint(client, monkeypatch):
    called = []
    monkeypatch.setattr(server, "authenticate_google", lambda: called.append(True))
    monkeypatch.setattr(server, "check_auth_status", lambda: {"authenticated": True, "has_credentials": True, "message": "OK"})
    resp = client.post("/auth/login", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    assert len(called) == 1

