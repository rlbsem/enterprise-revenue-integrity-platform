import copy

import httpx
import pytest

from integrity.control import Controller, Refused
from integrity.finance import publish
from integrity.finance_demo import execute_finance
from integrity.ledger import record
from integrity.lifecycle_demo import commands

from .helpers import rows


def test_reordered_conflicting_and_duplicate_commercial_events_preserve_receipts(stack, observe):
    controller = Controller(stack.config)
    controller.ingest(stack.events[1])
    controller.drain()
    first, second = commands(stack.events[1])[:2]
    with httpx.Client(
        base_url=stack.config["services"]["salesforce"]["url"],
        headers={"Authorization": "Bearer " + stack.injector},
        trust_env=False,
        timeout=10,
    ) as client:
        reorder = client.post("/test/contract-changes", json=second)
        assert reorder.status_code == 409
        assert client.post("/test/contract-changes", json=first).status_code == 200
        duplicate = client.post("/test/contract-changes", json=first)
        assert duplicate.status_code == 200 and duplicate.json()["duplicate"]
        conflict = client.post(
            "/test/contract-changes", json={**first, "quantity": first["quantity"] + 1}
        )
        assert conflict.status_code == 409
    assert controller.ingest(first)["status"] == "planned"
    controller.drain()
    before = {d: len(rows(stack, d, "effects")) for d in ["stripe", "netsuite"]}
    assert controller.ingest(first)["duplicate"]
    controller.drain()
    assert (
        before
        == {d: len(rows(stack, d, "effects")) for d in ["stripe", "netsuite"]}
        == {"stripe": 2, "netsuite": 2}
    )
    assert (
        len([x for x in rows(stack, "salesforce", "events") if x["id"] == first["event_id"]]) == 1
    )
    observe(
        reordered_HTTP_status=reorder.status_code,
        conflict_HTTP_status=conflict.status_code,
        duplicate_source_delivery=duplicate.json(),
        independent_effects=before,
        gate=controller.verify_recovery(),
    )


def test_direct_source_arithmetic_and_failed_publication_cannot_clear_open_variance(
    stack, tmp_path, observe
):
    result = execute_finance(stack, tmp_path / "financial")
    controller = Controller(stack.config)
    release = result["final_release"]
    # Separate source-database observation and arithmetic; no app oracle or SQL model formulas.
    billing = [x["body"] for x in rows(stack, "stripe", "records")]
    finance = [x["body"] for x in rows(stack, "netsuite", "records")]
    latest = {}
    for service in ["stripe", "netsuite"]:
        for entry in rows(stack, service, "source_events"):
            x = entry["body"]
            key = (service, x["tenant"], x["entity"], x["logical_id"])
            if key not in latest or latest[key]["revision"] < x["revision"]:
                latest[key] = x
    expected_billing = sum(x["amount_cents"] for x in billing if x["kind"] == "invoice_line")
    expected_finance = sum(x["amount_cents"] for x in finance if x["kind"] == "posting")
    for (service, *_), x in latest.items():
        sign = -1 if x["kind"] in ["credit", "credit_posting"] else 1
        if service == "stripe":
            expected_billing += sign * x["amount_cents"]
        else:
            expected_finance += sign * x["amount_cents"]
    cash = sum(x["amount_cents"] for x in billing if x["kind"] == "payment_allocation")
    applications = sum(x["amount_cents"] for x in finance if x["kind"] == "payment_application")
    actual = {
        k: sum(x[k] for x in release["rows"])
        for k in [
            "billing_net_cents",
            "erp_net_cents",
            "allocated_cash_cents",
            "posting_variance_cents",
        ]
    }
    expected = {
        "billing_net_cents": expected_billing,
        "erp_net_cents": expected_finance,
        "allocated_cash_cents": cash,
        "posting_variance_cents": expected_finance - expected_billing,
    }
    assert actual == expected and cash == applications
    assert expected["posting_variance_cents"] == -150000
    record(controller, result["source_snapshot"], release)
    current = tmp_path / "financial/releases/CURRENT.json"
    before = current.read_bytes()
    corrupted = copy.deepcopy(release["rows"])
    corrupted[0]["billing_net_cents"] += 1
    with pytest.raises((Refused, ValueError)):
        publish(current.parent, result["source_snapshot"], corrupted, result["source_snapshot"])
    assert current.read_bytes() == before
    cases = [
        x
        for x in rows(stack, "control", "financial_cases")
        if x["category"] == "unposted_adjustment_requires_review"
    ]
    assert cases and all(x["state"] == "open" for x in cases)
    assert sum(x["amount_cents"] for x in cases) in [-150000, 150000]
    observe(
        independent_direct_source_database_totals=expected,
        actual_published_totals=actual,
        independent_cash_application_cents=applications,
        source_event_revisions=len(latest),
        failed_one_cent_publication_preserved_pointer=True,
        unresolved_cases=[
            {k: x[k] for k in ["tenant", "entity", "category", "state", "amount_cents"]}
            for x in cases
        ],
        release_state=release["state"],
        native_vendor_execution=False,
    )
