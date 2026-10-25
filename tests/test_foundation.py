import copy
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from integrity.contracts import Commercial
from integrity.control import Controller, Refused
from integrity.db import connect


def rows(stack, table="outbox", database="control"):
    with connect(stack.admins[database]) as c:
        return c.execute(f"SELECT * FROM {table}").fetchall()


def planned(stack, controller, index=0):
    assert controller.ingest(stack.events[index])["status"] == "planned"
    return rows(stack)[0]


def test_end_to_end_two_entities_exact_cents(stack, controller):
    for event in stack.events[:2]:
        assert controller.ingest(event)["status"] == "planned"
    controller.drain()
    assert {r["status"] for r in rows(stack)} == {"verified"}
    assert len(rows(stack)) == 4
    assert {r["status"] for r in rows(stack, "inbox")} == {"completed"}
    billing = [r["body"] for r in rows(stack, "records", "stripe")]
    finance = [r["body"] for r in rows(stack, "records", "netsuite")]
    assert sum(r["amount_cents"] for r in billing if r["kind"] == "invoice") == 4200000
    assert sum(r["amount_cents"] for r in finance if r["kind"] == "posting") == 4200000
    assert sum(r["amount_cents"] for r in finance if r["kind"] == "payment_application") == 1650000
    assert {r["entity"] for r in finance} == {"US", "EU"}
    assert len(rows(stack, "effects", "stripe")) == 2
    assert len(rows(stack, "effects", "netsuite")) == 2


def test_duplicate_source_event_and_request(stack, controller):
    intent = planned(stack, controller)
    assert controller.ingest(stack.events[0])["duplicate"] is True
    assert len(rows(stack)) == 1
    assert controller.step(intent["effect_id"]) == "verified"
    with controller.client("stripe") as client:
        response = client.post(
            "/effects",
            json=intent["payload"],
            headers={"Idempotency-Key": str(intent["effect_id"])},
        )
        assert response.status_code == 200
        changed = copy.deepcopy(intent["payload"])
        changed["facts"]["quantity"] = 9
        assert (
            client.post(
                "/effects", json=changed, headers={"Idempotency-Key": str(intent["effect_id"])}
            ).status_code
            == 409
        )
    assert len(rows(stack, "effects", "stripe")) == 1
    assert len([r for r in rows(stack, "records", "stripe") if r["kind"] == "invoice"]) == 1


def set_fault(stack, controller, intent, modes):
    service = stack.config["services"][intent["destination"]]
    response = httpx.post(
        service["url"] + "/test/faults",
        json={"effect_id": str(intent["effect_id"]), "modes": modes},
        headers={"Authorization": "Bearer " + stack.injector},
        trust_env=False,
    )
    assert response.status_code == 200


def test_lost_ack_actual_committed_once_and_readback(stack, controller):
    intent = planned(stack, controller)
    set_fault(stack, controller, intent, ["lost_ack"])
    assert controller.step(intent["effect_id"], timeout=0.25) == "uncertain"
    assert len(rows(stack, "effects", "stripe")) == 1  # Commit preceded timed-out HTTP response.
    assert controller.step(intent["effect_id"]) == "verified"
    assert len(rows(stack, "effects", "stripe")) == 1
    assert len([r for r in rows(stack, "requests", "stripe") if r["operation"] == "apply"]) == 1
    assert rows(stack)[0]["proof"]["payload_hash"] == intent["hash"]


def test_receipt_reconciliation_is_not_new_action_authorization(stack, controller):
    intent = planned(stack, controller)
    set_fault(stack, controller, intent, ["lost_ack"])
    assert controller.step(intent["effect_id"], timeout=0.25) == "uncertain"
    with connect(stack.admins["control"]) as c:
        c.execute("UPDATE mappings SET status='revoked' WHERE source='netsuite'")
    assert controller.step(intent["effect_id"]) == "verified"
    assert len(rows(stack)) == 1  # Old billing receipt retained; no newly authorized ERP action.
    assert rows(stack, "exceptions")[0]["reason"].startswith("new_finance_intent_blocked")


def test_ambiguous_mapping_blocks_without_effect(stack, controller):
    result = controller.ingest(stack.events[2])
    assert result["detail"] == "ambiguous_or_missing_canonical_mapping"
    assert rows(stack) == []
    assert len(rows(stack, "exceptions")) == 1


def test_cross_entity_mapping_cannot_resolve(stack, controller):
    with connect(stack.admins["control"]) as c:
        c.execute("UPDATE mappings SET status='revoked' WHERE entity='US' AND source='salesforce'")
        c.execute(
            "INSERT INTO mappings VALUES('northstar','EU','canonical-2','salesforce',%s,2,'approved','wrong-entity-test')",
            (stack.events[0]["account_id"],),
        )
    assert controller.ingest(stack.events[0])["status"] == "blocked"
    assert rows(stack) == []


def test_unauthorized_financial_source_overwrite_is_rejected(stack, controller):
    event = {**stack.events[0], "collected_cash_cents": 99999999}
    with pytest.raises(ValidationError):
        controller.ingest(event)
    assert rows(stack, "inbox") == []


def test_forged_source_and_changed_owner_block_dispatch(stack, controller):
    forged = {**stack.events[0], "quantity": 100}
    assert controller.ingest(forged)["detail"] == "source_event_exact_content_mismatch"
    stack.reset()
    intent = planned(stack, controller)
    with connect(stack.admins["salesforce"]) as c:
        changed = {**stack.events[0], "owner_id": "005-new-owner"}
        c.execute(
            "UPDATE opportunities SET body=%s WHERE id=%s",
            (Jsonb(changed), changed["opportunity_id"]),
        )
    assert controller.step(intent["effect_id"]) == "blocked"
    assert rows(stack, "effects", "stripe") == []


def test_event_identity_collision_preserves_original(stack, controller):
    planned(stack, controller)
    with pytest.raises(Refused, match="event_identity_content_conflict"):
        controller.ingest({**stack.events[0], "quantity": 9})
    assert rows(stack, "inbox")[0]["body"] == stack.events[0]


def test_current_account_missing_or_outside_scope_blocks(stack, controller):
    with connect(stack.admins["salesforce"]) as c:
        c.execute("UPDATE accounts SET entity='EU' WHERE id=%s", (stack.events[0]["account_id"],))
    assert controller.ingest(stack.events[0])["detail"] == "source_record_or_scope_missing"
    assert rows(stack) == []
    assert rows(stack, "effects", "stripe") == []


def test_interrupted_before_dispatch_durable_intent_survives(stack, controller):
    planned(stack, controller)
    replacement = Controller(stack.config)
    replacement.drain()
    assert all(r["status"] == "verified" for r in rows(stack))
    assert len(rows(stack, "effects", "stripe")) == 1


def test_bounded_failure_enters_exhaustion(stack, controller):
    intent = planned(stack, controller)
    set_fault(stack, controller, intent, ["unavailable"] * 3)
    for _ in range(3):
        assert controller.step(intent["effect_id"]) == "uncertain"
    assert controller.step(intent["effect_id"]) == "exhausted"
    assert rows(stack)[0]["attempts"] == 3
    assert rows(stack, "effects", "stripe") == []
    assert rows(stack, "exceptions")[0]["reason"] == "retry_budget_exhausted"


def test_genuine_process_death_releases_worker_lock_and_recovers(stack, controller, tmp_path):
    intent = planned(stack, controller)
    marker = tmp_path / "commit-observed"
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "integrity.control",
            str(stack.config_path),
            str(intent["effect_id"]),
            "--crash-marker",
            str(marker),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.05)
        assert marker.exists(), "child did not reach real post-HTTP-commit boundary"
        assert len(rows(stack, "effects", "stripe")) == 1
        assert rows(stack)[0]["proof"] is None
        assert controller.step(intent["effect_id"]) == "busy"
        child.kill()
        child.wait(timeout=8)
        assert child.returncode != 0
        assert controller.step(intent["effect_id"]) == "verified"
        assert len(rows(stack, "effects", "stripe")) == 1
        observed = {
            "actual_subprocess_terminated": True,
            "exit_code": child.returncode,
            "remote_effects_before_kill": 1,
            "remote_effects_after_restart": 1,
            "competing_worker": "busy",
            "recovered_state": "verified",
        }
        output = os.environ.get("INTEGRITY_TEST_EVIDENCE")
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            (Path(output) / "process-death.json").write_text(json.dumps(observed, indent=2))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_actual_simultaneous_duplicate_dispatch(stack, controller):
    intent = planned(stack, controller)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: controller.step(intent["effect_id"]), range(2)))
    assert "verified" in results
    assert len(rows(stack, "effects", "stripe")) == 1


def test_database_boundaries_and_append_only_receipts(stack, controller):
    planned(stack, controller)
    controller.drain()
    with connect(stack.config["control_dsn"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE mappings SET status='revoked'")
    with connect(stack.app_dsns["stripe"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM effects")
    with connect(stack.admins["stripe"]) as c:
        with pytest.raises(psycopg.errors.RaiseException, match="append_only_record"):
            c.execute("UPDATE effects SET hash='tampered'")


def test_scope_readback_and_injector_permission(stack, controller):
    intent = planned(stack, controller)
    controller.step(intent["effect_id"])
    with controller.client("stripe") as client:
        assert (
            client.get(
                "/receipts/" + str(intent["effect_id"]), params={"tenant": "other", "entity": "US"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/test/faults", json={"effect_id": str(intent["effect_id"]), "modes": []}
            ).status_code
            == 401
        )


def test_independent_business_readback_rejects_tampered_content(stack, controller):
    intent = planned(stack, controller)
    set_fault(stack, controller, intent, ["lost_ack"])
    assert controller.step(intent["effect_id"], timeout=0.25) == "uncertain"
    # Deliberate storage-corruption injection by the test administrator, never an app privilege.
    with connect(stack.admins["stripe"]) as c:
        c.execute("ALTER TABLE records DISABLE TRIGGER immutable_record")
        c.execute(
            "UPDATE records SET body=jsonb_set(body,'{amount_cents}','3000001') WHERE kind='invoice'"
        )
        c.execute("ALTER TABLE records ENABLE TRIGGER immutable_record")
    assert controller.step(intent["effect_id"]) == "blocked"
    assert rows(stack)[0]["detail"] == "independent_record_readback_mismatch"
    assert len(rows(stack)) == 1


def test_receiver_database_credentials_cannot_open_finance_database(stack):
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    target = conninfo_to_dict(stack.admins["netsuite"])["dbname"]
    with pytest.raises(psycopg.OperationalError, match="permission denied for database"):
        with psycopg.connect(make_conninfo(stack.app_dsns["stripe"], dbname=target)):
            pass


@pytest.mark.parametrize(
    "field,value", [("quantity", True), ("unit_monthly_cents", 12.4), ("currency", "CAD")]
)
def test_money_and_quantity_contract_no_coercion(stack, field, value):
    with pytest.raises(ValidationError):
        Commercial.model_validate({**stack.events[0], field: value})
