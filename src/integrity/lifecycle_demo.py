import argparse
import json
from pathlib import Path

import httpx

from .control import Controller
from .contracts import digest
from .finance import extract, load, build, oracle, oracle_period, publish
from .finance_demo import execute_finance, SCOPES, compare_models
from .harness import LocalStack


def commands(base):
    definitions = [
        ("expansion", "expand-eu", 120000, 5, "2026-02-01", "2027-01-01", "2026-01-25", None),
        (
            "correction",
            "expand-eu",
            110000,
            5,
            "2026-02-01",
            "2027-01-01",
            "2026-02-15",
            "change-eu-2",
        ),
        ("contraction", "contract-eu", 90000, 5, "2026-03-01", "2027-01-01", "2026-02-20", None),
        ("cancellation", "cancel-eu", 0, 0, "2026-03-01", "2027-01-01", "2026-03-15", None),
        ("renewal", "renew-eu", 100000, 5, "2027-01-01", "2028-01-01", "2026-12-15", None),
    ]
    parent = base["event_id"]
    result = []
    for revision, (action, logical, unit, quantity, start, end, observed, corrects) in enumerate(
        definitions, 2
    ):
        event = {
            k: base[k]
            for k in (
                "tenant",
                "entity",
                "opportunity_id",
                "account_id",
                "contract_id",
                "owner_id",
                "currency",
            )
        }
        event.update(
            schema_version="contract-change.v1",
            source="salesforce",
            event_id=f"change-eu-{revision}",
            revision=revision,
            parent_event_id=parent,
            change_id=logical,
            action=action,
            correction_of=corrects,
            unit_monthly_cents=unit,
            quantity=quantity,
            effective_start=start,
            effective_end=end,
            event_at=observed + "T10:00:00+00:00",
            observed_source_at=observed + "T10:05:00+00:00",
        )
        result.append(event)
        parent = event["event_id"]
    return result


def apply_change(stack, controller, event):
    with httpx.Client(
        base_url=stack.config["services"]["salesforce"]["url"],
        headers={"Authorization": "Bearer " + stack.injector},
        trust_env=False,
    ) as client:
        response = client.post("/test/contract-changes", json=event)
        response.raise_for_status()
    result = controller.ingest(event)
    if result["status"] != "planned":
        raise AssertionError(result)
    controller.drain()
    return {"source_response": response.json(), "controller": result}


def execute_lifecycle(stack, workspace):
    workspace = Path(workspace)
    initial = execute_finance(stack, workspace / "initial-finance")
    controller = Controller(stack.config)
    actions = commands(stack.events[1])
    receipts = []
    for event in actions:
        receipts.append(apply_change(stack, controller, event))
    snapshot = extract(controller, SCOPES)
    database = workspace / "lifecycle.duckdb"
    load(database, snapshot)
    rows, gates = build(database, workspace / "lifecycle-dbt")
    assert rows == oracle(snapshot) and gates["period_rows"] == oracle_period(snapshot)
    # Rebuild the existing incremental target after a historical source cut.
    historical = extract(
        controller, SCOPES, {**snapshot["period"], "known_at": "2026-02-10T23:59:59+00:00"}
    )
    load(database, historical)
    old_rows, old_gates = build(database, workspace / "historical-dbt")
    assert old_rows == oracle(historical) and old_gates["period_rows"] == oracle_period(historical)
    load(database, snapshot)
    rows, incremental = build(database, workspace / "incremental-dbt")
    independent = workspace / "full.duckdb"
    load(independent, snapshot)
    full_rows, full = build(independent, workspace / "full-dbt", full_refresh=True)
    assert rows == full_rows == oracle(snapshot)
    compare_models(database, independent)
    from .portability import verify

    portability = verify(database, workspace / "incremental-dbt", stack.admin)
    release = publish(workspace / "releases", snapshot, rows, extract(controller, SCOPES))
    from .ledger import record

    ledger_runs = record(controller, snapshot, release)
    assert sum(r["posting_variance_cents"] for r in rows) == -150000
    renewal_period = {
        "start": "2027-01-01",
        "end_exclusive": "2027-04-01",
        "known_at": "2027-03-31T23:59:59+00:00",
    }
    renewal = extract(controller, SCOPES, renewal_period)
    renewal_db = workspace / "renewal.duckdb"
    load(renewal_db, renewal)
    renewal_rows, renewal_gates = build(renewal_db, workspace / "renewal-dbt")
    assert renewal_rows == oracle(renewal) and renewal_gates["period_rows"] == oracle_period(
        renewal
    )
    return {
        "lane": "local_contract_lifecycle_and_financial_acceptance",
        "source_snapshot": snapshot,
        "source_hash": digest(snapshot),
        "commercial_events": actions,
        "receipts": receipts,
        "initial_posting_variance_cents": -150000,
        "current_release": release,
        "durable_financial_runs": ledger_runs,
        "historical_rows": old_rows,
        "historical_measures": old_gates["period_rows"],
        "current_measures": gates["period_rows"],
        "incremental": incremental,
        "independent_full": full,
        "six_model_exact_equivalence": True,
        "portability": portability,
        "renewal_rows": renewal_rows,
        "renewal_measures": renewal_gates["period_rows"],
        "initial_finance": initial["final_release"],
        "native_vendor_execution": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    a = parser.parse_args()
    output = Path(a.output)
    if output.exists():
        raise ValueError("fresh_evidence_required")
    with LocalStack(a.workspace) as stack:
        result = execute_lifecycle(stack, stack.workspace)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes((json.dumps(result, indent=2, default=str) + "\n").encode())
    print(
        json.dumps(
            {
                "source_changes": len(result["commercial_events"]),
                "six_models_exact": True,
                "variance_cents": sum(
                    r["posting_variance_cents"] for r in result["current_release"]["rows"]
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
