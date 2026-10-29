import socket
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from integrity.portable import serve, current, private, lock
from integrity.console_api import create_app


def test_portable_lock_and_owned_pointer(tmp_path):
    state = tmp_path / "state"
    with lock(state):
        with pytest.raises(ValueError, match="already_in_use"):
            with lock(state):
                pass
        private(state / "ready-private.json", {"run": "../other"})
    with pytest.raises(ValueError, match="invalid_owned_run"):
        current(state)


def test_port_conflict_fails_before_fixture(tmp_path, monkeypatch):
    monkeypatch.delenv("INTEGRITY_ADMIN_DSN", raising=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        with pytest.raises(OSError):
            serve(tmp_path / "never-created", s.getsockname()[1])
    assert not (tmp_path / "never-created").exists()


def test_external_runtime_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("INTEGRITY_ADMIN_DSN", "must-not-use")
    with pytest.raises(ValueError, match="refuses_external_runtime"):
        serve(tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


def test_portable_host_boundary_and_security_headers(stack):
    config = {**stack.config, "console_allowed_host": "127.0.0.1:8582"}
    with TestClient(create_app(config, origin="http://127.0.0.1:8582", start_worker=False)) as c:
        assert c.get("/", headers={"Host": "attacker.example"}).status_code == 400
        response = c.get("/", headers={"Host": "127.0.0.1:8582"})
        assert response.status_code == 200 and response.headers["content-security-policy"]
        assert c.get("/api/overview", headers={"Host": "127.0.0.1:8582"}).status_code == 401
        assert c.get("/openapi.json", headers={"Host": "127.0.0.1:8582"}).status_code == 404


def test_compose_has_only_loopback_console_and_allowlisted_context():
    text = (Path(__file__).resolve().parents[1] / "compose.yaml").read_text()
    assert "127.0.0.1:${INTEGRITY_DEMO_PORT:-8582}:8080" in text
    assert "read_only: true" in text and "cap_drop: [ALL]" in text
    assert "network_mode:" not in text and "privileged:" not in text
    dockerignore = (Path(__file__).resolve().parents[1] / ".dockerignore").read_text()
    assert dockerignore.startswith("**\n") and "!src/**" in dockerignore
