import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo, conninfo_to_dict
import pytest

from integrity.control import Controller, Refused
from integrity.db import connect
from integrity.operations import propose, decide, execute
from integrity.security import authenticate, session
from integrity.finance import publish
from integrity.publication import recover
from test_foundation import planned, rows, set_fault


def exhausted(stack, controller):
    intent = planned(stack, controller)
    set_fault(stack, controller, intent, ["unavailable"] * 3)
    for _ in range(3):
        assert controller.step(intent["effect_id"]) == "uncertain"
    assert controller.step(intent["effect_id"]) == "exhausted"
    return intent


def test_database_rls_without_client_filters(stack, controller):
    for event in stack.events[:2]:
        controller.ingest(event)
    controller.drain()
    with connect(stack.operators["reader_eu"]["dsn"]) as c:
        assert {r["entity"] for r in c.execute("SELECT * FROM outbox")} == {"EU"}
        c.execute("SET app.tenant='northstar'")
        assert c.execute("SELECT count(*) AS n FROM outbox WHERE entity='US'").fetchone()["n"] == 0
    with connect(stack.operators["outsider"]["dsn"]) as c:
        assert c.execute("SELECT count(*) AS n FROM inbox").fetchone()["n"] == 0
    with connect(stack.operators["operator"]["dsn"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE outbox SET max_attempts=6")


def test_authenticated_session_revocation_and_login_budget(stack):
    actor = stack.operators["operator"]
    issued = authenticate(stack.config, actor["username"], actor["password"])
    assert issued and session(stack.config, issued[0])["username"] == "operator"
    with connect(stack.admins["control"]) as c:
        c.execute("UPDATE operator_users SET active=false WHERE username='operator'")
    assert session(stack.config, issued[0]) is None
    with connect(actor["dsn"]) as c:
        assert c.execute("SELECT count(*) AS n FROM accounts").fetchone()["n"] == 0
    for _ in range(5):
        assert authenticate(stack.config, "auditor", "incorrect") is None
    assert authenticate(stack.config, "auditor", stack.operators["auditor"]["password"]) is None


def test_exact_two_person_bounded_recovery(stack, controller):
    intent = exhausted(stack, controller)
    identifier = propose(
        stack.operators["operator"],
        intent["effect_id"],
        "Receiver outage cleared; retry exact intent",
    )
    assert execute(controller, identifier) == "not_approved"
    with pytest.raises(Refused):
        decide(stack.operators["operator"], identifier, "approved", "Self approval")
    decide(stack.operators["approver"], identifier, "approved", "Exact payload and source reviewed")
    assert execute(controller, identifier) == "verified"
    controller.drain()
    target = next(r for r in rows(stack) if r["effect_id"] == intent["effect_id"])
    assert (
        target["attempts"] == 4 and target["max_attempts"] == 6 and target["status"] == "verified"
    )
    assert len(rows(stack, "effects", "stripe")) == 1
    assert execute(controller, identifier) == "not_approved"
    with pytest.raises(psycopg.Error):
        propose(stack.operators["operator"], intent["effect_id"], "Unlimited retry attempt")


def test_rejected_review_is_not_execution(stack, controller):
    intent = exhausted(stack, controller)
    identifier = propose(stack.operators["operator"], intent["effect_id"], "Review exact retry")
    decide(stack.operators["approver"], identifier, "rejected", "Upstream ownership not confirmed")
    assert execute(controller, identifier) == "not_approved"
    assert rows(stack)[0]["max_attempts"] == 3 and not rows(stack, "effects", "stripe")


@pytest.mark.parametrize("change", ["revoked", "owner_changed", "expired"])
def test_stale_approval_cannot_authorize_new_effect(stack, controller, change):
    intent = exhausted(stack, controller)
    identifier = propose(stack.operators["operator"], intent["effect_id"], "Review scoped retry")
    decide(stack.operators["approver"], identifier, "approved", "Exact content reviewed")
    if change == "revoked":
        with connect(stack.admins["control"]) as c:
            c.execute("UPDATE operator_users SET active=false WHERE username='approver'")
    elif change == "owner_changed":
        with connect(stack.admins["salesforce"]) as c:
            c.execute("UPDATE opportunities SET body=jsonb_set(body,'{owner_id}','\"new-owner\"')")
    else:
        # Fault injection changes only expiry through the test administrator, with trigger restored.
        with connect(stack.admins["control"]) as c:
            c.execute("ALTER TABLE proposals DISABLE TRIGGER immutable_proposal")
            c.execute("UPDATE proposals SET expires_at=clock_timestamp()-interval '1 second'")
            c.execute("ALTER TABLE proposals ENABLE TRIGGER immutable_proposal")
    assert execute(controller, identifier) == "invalidated"
    assert not rows(stack, "effects", "stripe")
    assert rows(stack)[0]["max_attempts"] == 3


def test_sql_guards_independent_of_application_roles(stack, controller):
    intent = exhausted(stack, controller)
    forged = {**stack.operators["reader_eu"], "role": "operator"}
    with pytest.raises(Refused, match="effect_not_found_in_scope"):
        propose(forged, intent["effect_id"], "Wrong legal entity")
    identifier = propose(stack.operators["operator"], intent["effect_id"], "Review")
    with connect(stack.admins["control"]) as c:
        c.execute(
            "UPDATE operator_scope SET role='approver' WHERE db_role=%s",
            (stack.operators["operator"]["db_role"],),
        )
        c.execute(
            sql.SQL("GRANT INSERT ON decisions TO {}").format(
                sql.Identifier(stack.operators["operator"]["db_role"])
            )
        )
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="separation_denied"):
            decide(
                {**stack.operators["operator"], "role": "approver"},
                identifier,
                "approved",
                "Self approval after role change",
            )
    finally:
        with connect(stack.admins["control"]) as c:
            c.execute(
                sql.SQL("REVOKE INSERT ON decisions FROM {}").format(
                    sql.Identifier(stack.operators["operator"]["db_role"])
                )
            )


def test_stale_worker_cannot_overwrite_verified_state(stack, controller):
    old = planned(stack, controller)
    assert controller.step(old["effect_id"]) == "verified"
    with pytest.raises(Refused, match="stale_worker_fence"):
        controller.mark(old, "blocked", "stale worker result")
    current = next(r for r in rows(stack) if r["effect_id"] == old["effect_id"])
    assert current["status"] == "verified" and current["fence"] > old["fence"]


def test_actual_restored_control_blocks_remote_ahead(stack, controller):
    backup = stack.tag + "_restore_" + uuid4().hex[:6]
    original = conninfo_to_dict(stack.admins["control"])["dbname"]
    with psycopg.connect(stack.admin, autocommit=True) as c:
        c.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(backup), sql.Identifier(original)
            )
        )
    try:
        planned(stack, controller)
        controller.drain()
        restored = Controller(
            {
                **stack.config,
                "control_dsn": make_conninfo(stack.config["control_dsn"], dbname=backup),
            }
        )
        gate = restored.verify_recovery()
        assert gate["safe_to_resume"] is False
        assert {r["reason"] for r in gate["issues"]} == {"remote_ahead_missing_intent"}
        assert restored.ingest(stack.events[1])["status"] == "planned"
        with connect(make_conninfo(stack.admins["control"], dbname=backup)) as c:
            intent = c.execute("SELECT effect_id FROM outbox").fetchone()["effect_id"]
        assert restored.step(intent) == "blocked"
        assert len(rows(stack, "effects", "stripe")) == 1
        output = os.environ.get("INTEGRITY_TEST_EVIDENCE")
        if output:
            (Path(output) / "restored-state.json").write_bytes(
                (
                    json.dumps(
                        {
                            "local_postgresql_template_restore": True,
                            "independent_receiver_gate": gate,
                            "new_dispatch": "blocked",
                        },
                        indent=2,
                    )
                    + "\n"
                ).encode()
            )
    finally:
        with psycopg.connect(stack.admin, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE {} WITH(FORCE)").format(sql.Identifier(backup)))


def test_real_publication_process_death_and_explicit_recovery(financial_run, tmp_path):
    from integrity.publication import alive

    snapshot = financial_run["source_snapshot"]
    output = tmp_path / "releases"
    publish(output, snapshot, financial_run["final_release"]["rows"], snapshot)
    before = (output / "CURRENT.json").read_bytes()
    data = tmp_path / "inputs.json"
    data.write_text(
        json.dumps({"snapshot": snapshot, "rows": financial_run["final_release"]["rows"]})
    )
    marker = tmp_path / "staged"
    child = subprocess.Popen(
        [sys.executable, "-m", "integrity.publish_worker", str(data), str(output), str(marker)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.05)
        assert marker.exists()
        lock = json.loads((output / ".publish.lock").read_text())
        assert alive(child.pid)
        child.kill()
        child.wait(timeout=8)
        assert (output / "CURRENT.json").read_bytes() == before
        with pytest.raises(Refused, match="publication_lock_requires_owner_review"):
            publish(output, snapshot, financial_run["final_release"]["rows"], snapshot)
        recover(output, lock)
        publish(output, snapshot, financial_run["final_release"]["rows"], snapshot)
        assert (output / "CURRENT.json").read_bytes() == before
        destination = os.environ.get("INTEGRITY_TEST_EVIDENCE")
        if destination:
            (Path(destination) / "publication-process-death.json").write_bytes(
                (
                    json.dumps(
                        {
                            "actual_process_killed": True,
                            "pointer_preserved": True,
                            "stale_owner_blocked": True,
                            "explicit_verified_dead_recovery": True,
                        }
                    )
                    + "\n"
                ).encode()
            )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_reviewed_archive_rehydrates_only_verified_history(stack, controller):
    from integrity.recovery import archive, restore_history
    from integrity.contracts import digest

    original = conninfo_to_dict(stack.admins["control"])["dbname"]
    backup = stack.tag + "_rehydrate"
    with psycopg.connect(stack.admin, autocommit=True) as c:
        c.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(backup), sql.Identifier(original)
            )
        )
    try:
        planned(stack, controller)
        controller.drain()
        journal = archive(controller)
        restored = Controller(
            {
                **stack.config,
                "control_dsn": make_conninfo(stack.config["control_dsn"], dbname=backup),
            }
        )
        assert not restored.verify_recovery()["safe_to_resume"]
        with pytest.raises(Refused, match="digest_mismatch"):
            restore_history(restored, journal, "0" * 64)
        before = sum(
            r["operation"] in ("apply", "duplicate") for r in rows(stack, "requests", "stripe")
        )
        result = restore_history(restored, journal, digest(journal))
        assert result["restored_intents"] == 2 and result["remote_posts"] == 0
        assert restored.verify_recovery()["safe_to_resume"]
        assert restore_history(restored, journal, digest(journal))["restored_intents"] == 0
        assert (
            sum(r["operation"] in ("apply", "duplicate") for r in rows(stack, "requests", "stripe"))
            == before
        )
        destination = os.environ.get("INTEGRITY_TEST_EVIDENCE")
        if destination:
            (Path(destination) / "history-rehydration.json").write_bytes(
                (json.dumps(result, indent=2) + "\n").encode()
            )
    finally:
        with psycopg.connect(stack.admin, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE {} WITH(FORCE)").format(sql.Identifier(backup)))


def test_direct_sql_cannot_forge_incomplete_proposal_or_completed_recheck(stack, controller):
    from psycopg.types.json import Jsonb

    effect = exhausted(stack, controller)
    with pytest.raises(psycopg.errors.RaiseException, match="not_eligible"):
        with connect(stack.operators["operator"]["dsn"]) as c:
            c.execute(
                "INSERT INTO proposals(id,tenant,entity,kind,effect_id,payload,payload_hash,reason) VALUES(%s,'northstar','US','retry_effect',%s,%s,'forged','forged')",
                (uuid4(), effect["effect_id"], Jsonb({})),
            )
    with pytest.raises(psycopg.errors.RaiseException, match="state_denied"):
        with connect(stack.operators["operator"]["dsn"]) as c:
            c.execute(
                "INSERT INTO reconciliation_jobs(tenant,entity,state,reason) VALUES('northstar','US','verified','forged')"
            )
