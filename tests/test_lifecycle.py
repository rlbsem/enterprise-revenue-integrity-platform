import copy
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from integrity.control import Controller, Refused
from integrity.contracts import digest
from integrity.finance import oracle
from integrity.lifecycle_demo import commands, execute_lifecycle
from integrity.db import connect


@pytest.fixture(scope="session")
def lifecycle_run(runtime, tmp_path_factory):
    runtime.reset()
    result = execute_lifecycle(runtime, tmp_path_factory.mktemp("lifecycle-acceptance"))
    import json
    import os
    from pathlib import Path

    if os.environ.get("INTEGRITY_TEST_EVIDENCE"):
        (Path(os.environ["INTEGRITY_TEST_EVIDENCE"]) / "lifecycle.json").write_bytes(
            (json.dumps(result, indent=2, default=str) + "\n").encode()
        )
    return result


def test_lifecycle_financial_and_incremental_full_parity(lifecycle_run):
    result = lifecycle_run
    assert result["six_model_exact_equivalence"]
    assert len(result["commercial_events"]) == 5
    assert result["incremental"]["incremental_models"] == [
        "model.revenue_integrity.mart_reconciliation"
    ]
    assert not result["incremental"]["full_refresh"] and result["independent_full"]["full_refresh"]
    rows = result["current_release"]["rows"]
    assert sum(r["billing_net_cents"] for r in rows) == 3900000
    assert sum(r["erp_net_cents"] for r in rows) == 3750000
    assert sum(r["posting_variance_cents"] for r in rows) == -150000
    assert sum(r["period_end_arr_cents"] for r in result["current_measures"]) == 12000000
    assert sum(r["period_end_arr_cents"] for r in result["historical_measures"]) == 19200000
    assert sum(r["billing_net_cents"] for r in result["renewal_rows"]) == 1500000
    assert sum(r["period_end_arr_cents"] for r in result["renewal_measures"]) == 6000000


def test_real_postgresql_warehouse_portability(lifecycle_run):
    proof = lifecycle_run["portability"]
    assert proof["postgresql"] == "actual_local_execution"
    assert len(proof["model_comparisons"]) == 6
    assert all(r["all_typed_values_equal"] for r in proof["model_comparisons"])
    assert proof["snowflake_native_execution"] is False


def test_all_variance_categories_are_explicit_and_non_additive(lifecycle_run):
    from integrity.variance import CATEGORIES

    assert len(CATEGORIES) == 9
    bridge = lifecycle_run["current_release"]["variance_bridge"]
    assert bridge["non_additive_bases"]
    assert {
        "duplicate_source",
        "credit",
        "proration",
        "partial_settlement",
        "backdated_correction",
        "unmapped_legal_entity",
    }.issubset({r["category"] for r in bridge["entries"]})
    assert any(
        r["category"] == "unposted_adjustment_requires_review" and r["amount_cents"] == -150000
        for r in bridge["entries"]
    )


def test_durable_financial_cases_preserve_variance_and_history(runtime, lifecycle_run):
    # lifecycle_run last wrote to the shared runtime; other tests may reset it, so verify via a fresh replay when needed.
    from integrity.ledger import record

    controller = Controller(runtime.config)
    ids = record(controller, lifecycle_run["source_snapshot"], lifecycle_run["current_release"])
    assert ids == lifecycle_run["durable_financial_runs"]
    with connect(runtime.admins["control"]) as c:
        cases = c.execute(
            "SELECT * FROM financial_cases WHERE category='unposted_adjustment_requires_review'"
        ).fetchall()
        assert (
            len(cases) == 1 and cases[0]["amount_cents"] == -150000 and cases[0]["state"] == "open"
        )
        assert c.execute("SELECT count(*) AS n FROM financial_case_events").fetchone()["n"] > 0


def test_activation_gap_and_contract_mismatch_are_not_inferred_fraud(stack):
    from integrity.finance import extract, bridge

    controller = Controller(stack.config)
    controller.ingest(stack.events[0])
    controller.drain()
    snapshot = extract(controller, [("northstar", "US"), ("northstar", "EU")])
    report = bridge(snapshot, oracle(snapshot))
    assert any(r["category"] == "activation_lag" for r in report["entries"])
    # Independently observed commercial expansion before its billing effect: truthful mismatch.
    controller.ingest(stack.events[1])
    controller.drain()
    event = commands(stack.events[1])[0]
    lane = stack.config["services"]["salesforce"]
    response = httpx.post(
        lane["url"] + "/test/contract-changes",
        json=event,
        headers={"Authorization": "Bearer " + stack.injector},
        trust_env=False,
    )
    assert response.status_code == 200
    snapshot = extract(controller, [("northstar", "US"), ("northstar", "EU")])
    assert any(r["billing_contract_variance_cents"] == -400000 for r in oracle(snapshot))


def test_missing_parent_and_forged_billing_history_block(lifecycle_run):
    snapshot = copy.deepcopy(lifecycle_run["source_snapshot"])
    export = next(
        x for x in snapshot["snapshots"] if x["service"] == "salesforce" and x["entity"] == "EU"
    )
    export["contents"]["events"] = [
        x for x in export["contents"]["events"] if x["event_id"] != "change-eu-2"
    ]
    export["content_hash"] = digest(export["contents"])
    with pytest.raises(Refused, match="commercial_history_coverage_gap"):
        oracle(snapshot)
    snapshot = copy.deepcopy(lifecycle_run["source_snapshot"])
    export = next(
        x for x in snapshot["snapshots"] if x["service"] == "stripe" and x["entity"] == "EU"
    )
    next(r for r in export["contents"]["records"] if r["kind"] == "subscription_change")["change"][
        "unit_monthly_cents"
    ] += 1
    export["content_hash"] = digest(export["contents"])
    with pytest.raises(Refused, match="billing_change_source_provenance_mismatch"):
        oracle(snapshot)


def test_concurrent_source_compare_and_swap_and_scope(stack):
    controller = Controller(stack.config)
    controller.ingest(stack.events[1])
    controller.drain()
    event = commands(stack.events[1])[0]
    lane = stack.config["services"]["salesforce"]

    def send(body):
        return httpx.post(
            lane["url"] + "/test/contract-changes",
            json=body,
            headers={"Authorization": "Bearer " + stack.injector},
            trust_env=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                send, [event, {**event, "event_id": "competing-event", "change_id": "competing"}]
            )
        )
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = (
        event
        if results[0].status_code == 200
        else {**event, "event_id": "competing-event", "change_id": "competing"}
    )
    assert controller.ingest(winner)["status"] == "planned"
    controller.drain()
    assert controller.ingest(winner)["duplicate"]
    wrong = {**commands(stack.events[1])[1], "entity": "US"}
    assert send(wrong).status_code == 409
    with controller.client("salesforce") as client:
        assert client.post("/test/contract-changes", json=wrong).status_code == 401


def test_recorded_posting_date_explains_only_its_own_lag(lifecycle_run):
    from integrity.finance import bridge

    snapshot = copy.deepcopy(lifecycle_run["source_snapshot"])
    source = next(
        x for x in snapshot["snapshots"] if x["service"] == "netsuite" and x["entity"] == "US"
    )
    next(r for r in source["contents"]["records"] if r["kind"] == "posting")["posting_date"] = (
        "2026-04-01"
    )
    source["content_hash"] = digest(source["contents"])
    entries = bridge(snapshot, oracle(snapshot))["entries"]
    assert any(r["category"] == "posting_lag" and r["amount_cents"] == -3000000 for r in entries)
    assert any(
        r["category"] == "unposted_adjustment_requires_review" and r["amount_cents"] == -150000
        for r in entries
    )


def test_lifecycle_billing_commit_and_restart_once(stack):
    from test_foundation import set_fault, rows

    controller = Controller(stack.config)
    controller.ingest(stack.events[1])
    controller.drain()
    event = commands(stack.events[1])[0]
    lane = stack.config["services"]["salesforce"]
    response = httpx.post(
        lane["url"] + "/test/contract-changes",
        json=event,
        headers={"Authorization": "Bearer " + stack.injector},
        trust_env=False,
    )
    assert response.status_code == 200
    assert controller.ingest(event)["status"] == "planned"
    intent = next(r for r in rows(stack) if r["event_id"] == event["event_id"])
    set_fault(stack, controller, intent, ["lost_ack"])
    assert controller.step(intent["effect_id"], timeout=0.25) == "uncertain"
    assert Controller(stack.config).step(intent["effect_id"]) == "verified"
    controller.drain()
    with connect(stack.admins["stripe"]) as c:
        assert (
            c.execute("SELECT count(*) AS n FROM records WHERE kind='amendment_line'").fetchone()[
                "n"
            ]
            == 1
        )
