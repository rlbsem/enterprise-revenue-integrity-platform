from fastapi.testclient import TestClient

from integrity.console_api import create_app
from test_security import exhausted


def login(client, stack, username):
    client.headers["origin"] = "http://testserver"
    response = client.post(
        "/api/login", json={"username": username, "password": stack.operators[username]["password"]}
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def test_api_auth_scope_and_csrf(stack, controller):
    exhausted(stack, controller)
    with TestClient(
        create_app(stack.config, origin="http://testserver", start_worker=False)
    ) as client:
        assert client.get("/api/overview").status_code == 401
        login(client, stack, "reader_eu")
        assert client.get("/api/overview?entity=US").status_code == 403
        assert client.get("/api/overview?tenant=northstar").status_code == 403
        assert client.get("/api/overview").json()["effects"] == []
        assert (
            client.post(
                "/api/proposals", json={"effect_id": "not-an-id", "reason": "test"}
            ).status_code
            == 422
        )
        client.headers["X-CSRF-Token"] = "invalid"
        assert client.post("/api/logout").status_code == 403
        client.headers["origin"] = "https://unapproved.example"
        assert client.post("/api/login", json={"username": "x", "password": "y"}).status_code == 403


def test_api_exact_review_and_no_secret_export(stack, controller):
    intent = exhausted(stack, controller)
    with TestClient(
        create_app(stack.config, origin="http://testserver", start_worker=False)
    ) as client:
        login(client, stack, "operator")
        created = client.post(
            "/api/proposals",
            json={"effect_id": str(intent["effect_id"]), "reason": "Upstream outage cleared"},
        )
        assert created.status_code == 200
        identifier = created.json()["proposal_id"]
        assert (
            client.post(
                "/api/proposals/" + identifier + "/decision",
                json={"result": "approved", "reason": "Self approval"},
            ).status_code
            == 403
        )
        login(client, stack, "approver")
        assert (
            client.post(
                "/api/proposals/" + identifier + "/decision",
                json={"result": "approved", "reason": "Exact content reviewed"},
            ).status_code
            == 200
        )
        report = client.get("/api/export")
        assert report.status_code == 200
        for value in (
            stack.config["control_dsn"],
            stack.operators["operator"]["password"],
            stack.config["services"]["stripe"]["token"],
        ):
            assert value not in report.text
        assert "no-store" in report.headers["cache-control"]
        assert "script-src 'self'" in report.headers["content-security-policy"]
        assert client.post("/api/logout").status_code == 200
        assert client.get("/api/me").status_code == 401


def test_input_fields_cannot_escalate_actions(stack):
    with TestClient(
        create_app(stack.config, origin="http://testserver", start_worker=False)
    ) as client:
        login(client, stack, "operator")
        response = client.post(
            "/api/proposals",
            json={
                "effect_id": "00000000-0000-0000-0000-000000000000",
                "reason": "invalid action",
                "kind": "post_financial_amount",
                "amount_cents": 999,
            },
        )
        assert response.status_code == 422 and response.json() == {
            "detail": "invalid_request_contract"
        }
