import copy
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg
import pytest

from integrity.console import start_api
from integrity.control import Controller
from integrity.db import connect
from integrity.harness import free_port
from integrity.operations import propose, decide, execute

from .helpers import create_other_tenant, exhaust, rows


def test_populated_tenant_isolation_and_direct_database_escalation(stack, observe):
    create_other_tenant(stack)
    observations = {}
    for username, scope, count in [
        ("operator", ("northstar", "US"), 2),
        ("reader_eu", ("northstar", "EU"), 2),
        ("outsider", ("other", "US"), 2),
    ]:
        actor = stack.operators[username]
        with connect(actor["dsn"]) as c:
            flags = c.execute(
                "SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=session_user"
            ).fetchone()
            assert not any(flags.values())
            c.execute(
                "SELECT set_config('app.tenant','other',false),set_config('app.entity','US',false)"
            )
            actual = c.execute("SELECT tenant,entity,effect_id FROM outbox").fetchall()
            assert len(actual) == count and {(x["tenant"], x["entity"]) for x in actual} == {scope}
            policies = c.execute(
                "SELECT relname,relrowsecurity FROM pg_class WHERE relname IN ('outbox','inbox','audit','proposals','decisions','attempts','reconciliation_jobs')"
            ).fetchall()
            assert len(policies) == 7 and all(x["relrowsecurity"] for x in policies)
        denied = []
        for query in [
            "SET ROLE postgres",
            "SET SESSION AUTHORIZATION postgres",
            "UPDATE outbox SET status='verified'",
            "DELETE FROM audit",
        ]:
            with pytest.raises(psycopg.Error) as error:
                with connect(actor["dsn"]) as c:
                    c.execute(query)
            assert error.value.sqlstate == "42501"
            denied.append(error.value.sqlstate)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connect(actor["dsn"]) as c:
                c.execute("SET row_security=off")
                c.execute("SELECT * FROM outbox")
        observations[username] = {
            "observed_scope": scope,
            "visible_effects": len(actual),
            "restricted_role": True,
            "native_SQL_denial_states": denied,
        }
    observe(
        actors=observations,
        independent_receiver_effects={
            d: len(rows(stack, d, "effects")) for d in ["stripe", "netsuite"]
        },
    )


def test_real_http_scope_session_csrf_origin_and_closed_request_contract(stack, observe):
    create_other_tenant(stack)
    port = free_port()
    origin = f"http://127.0.0.1:{port}"
    stack.config["console_allowed_host"] = f"127.0.0.1:{port}"
    process = start_api(stack, port, origin=origin)
    statuses = {}
    with httpx.Client(base_url=origin, trust_env=False, timeout=10) as client:
        statuses["unauthenticated"] = client.get("/api/overview").status_code
        assert statuses["unauthenticated"] == 401
        for value in [None, "null", "https://unapproved.example"]:
            headers = {} if value is None else {"Origin": value}
            assert (
                client.post(
                    "/api/login",
                    json={
                        "username": "operator",
                        "password": stack.operators["operator"]["password"],
                    },
                    headers=headers,
                ).status_code
                == 403
            )
        assert client.get("/", headers={"Host": "attacker.example"}).status_code == 400
        client.headers["Origin"] = origin
        login = client.post(
            "/api/login",
            json={"username": "operator", "password": stack.operators["operator"]["password"]},
        )
        assert login.status_code == 200
        cookie = ";".join(login.headers.get_list("set-cookie")).lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        csrf = login.json()["csrf"]
        assert client.get("/api/overview?entity=EU").status_code == 403
        assert client.get("/api/overview?tenant=other").status_code == 403
        foreign = next(x for x in rows(stack, "control", "outbox") if x["tenant"] == "other")
        assert client.get("/api/lineage/" + str(foreign["effect_id"])).status_code == 404
        assert client.post("/api/logout").status_code == 403
        client.headers["X-CSRF-Token"] = "wrong"
        assert client.post("/api/logout").status_code == 403
        client.headers["X-CSRF-Token"] = csrf
        assert (
            client.post(
                "/api/proposals",
                json={"effect_id": str(foreign["effect_id"]), "reason": "Cross-tenant write"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/proposals",
                json={
                    "effect_id": str(foreign["effect_id"]),
                    "reason": "Extra fields",
                    "role": "approver",
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/proposals", content=b" " * 32769, headers={"Content-Type": "application/json"}
            ).status_code
            == 413
        )
        snapshot = client.get("/api/export")
        assert snapshot.status_code == 200 and "no-store" in snapshot.headers["cache-control"]
        assert all(
            e["tenant"] == "northstar" and e["entity"] == "US" for e in snapshot.json()["effects"]
        )
        for secret in [stack.config["control_dsn"], stack.operators["operator"]["password"], csrf]:
            assert secret not in snapshot.text
        old_cookies = copy.copy(client.cookies)
        assert client.post("/api/logout").status_code == 200
        client.cookies = old_cookies
        assert client.get("/api/me").status_code == 401
        statuses.update(
            cross_entity=403,
            cross_tenant=403,
            foreign_effect=404,
            csrf_missing=403,
            csrf_wrong=403,
            closed_contract=422,
            oversized=413,
            revoked_session=401,
        )
    observe(
        actual_loopback_HTTP=True,
        statuses=statuses,
        source_effects={d: len(rows(stack, d, "effects")) for d in ["stripe", "netsuite"]},
        operator_export="US_only",
        credentials_recorded=False,
    )
    process.terminate()
    process.wait(timeout=15)


def test_competing_approved_retries_cannot_multiply_effects(stack, observe):
    controller = Controller(stack.config)
    intent = exhaust(stack, controller)
    proposals = [
        propose(
            stack.operators["operator"], intent["effect_id"], "Independent exact retry " + str(n)
        )
        for n in range(2)
    ]
    for identifier in proposals:
        decide(stack.operators["approver"], identifier, "approved", "Reviewed bounded exact retry")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(lambda identifier: execute(Controller(stack.config), identifier), proposals)
        )
    controller.drain(due_only=False)
    effects = rows(stack, "control", "outbox")
    assert len(rows(stack, "stripe", "effects")) == len(rows(stack, "netsuite", "effects")) == 1
    # Three rejected outage attempts are requests, not committed business effects.
    assert (
        len(
            [
                r
                for r in rows(stack, "stripe", "requests")
                if r["operation"] == "apply" and r["status"] == 200
            ]
        )
        == 1
    )
    assert max(e["max_attempts"] for e in effects) == 6
    assert all(e["status"] == "verified" for e in effects)
    observe(
        outcomes=outcomes,
        receiver_effects={"stripe": 1, "netsuite": 1},
        successful_billing_applications=1,
        proposal_states=[r["state"] for r in rows(stack, "control", "proposals")],
        attempt_budget=max(e["max_attempts"] for e in effects),
    )


@pytest.mark.parametrize("change", ["proposer_revoked", "approver_scope_removed", "expired"])
def test_current_approval_authority_is_rechecked_after_decision(stack, change, observe):
    controller = Controller(stack.config)
    intent = exhaust(stack, controller)
    identifier = propose(
        stack.operators["operator"], intent["effect_id"], "Review after receiver outage"
    )
    decide(stack.operators["approver"], identifier, "approved", "Exact bounded grant")
    with connect(stack.admins["control"]) as c:
        if change == "proposer_revoked":
            c.execute("UPDATE operator_users SET active=false WHERE username='operator'")
        elif change == "approver_scope_removed":
            c.execute(
                "DELETE FROM operator_scope WHERE db_role=%s",
                (stack.operators["approver"]["db_role"],),
            )
        else:
            c.execute("ALTER TABLE proposals DISABLE TRIGGER immutable_proposal")
            c.execute(
                "UPDATE proposals SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
                (identifier,),
            )
            c.execute("ALTER TABLE proposals ENABLE TRIGGER immutable_proposal")
    outcome = execute(controller, identifier)
    assert outcome == "invalidated"
    assert (
        not rows(stack, "stripe", "effects")
        and rows(stack, "control", "outbox")[0]["max_attempts"] == 3
    )
    observe(
        changed_authority=change,
        outcome=outcome,
        receiver_effects=0,
        maximum_attempts=3,
        audit_actions=[r["action"] for r in rows(stack, "control", "audit")],
    )
