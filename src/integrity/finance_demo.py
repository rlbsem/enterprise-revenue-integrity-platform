import argparse
import json
from pathlib import Path

import duckdb
import httpx

from .control import Controller
from .contracts import digest
from .demo import execute
from .finance import build, extract, load, oracle, oracle_period, publish
from .harness import LocalStack

SCOPES = [("northstar", "US"), ("northstar", "EU")]


def inject(stack, source, body):
    response = httpx.post(
        stack.config["services"][source]["url"] + "/test/source-events",
        json=body,
        headers={"Authorization": "Bearer " + stack.injector},
        trust_env=False,
    )
    response.raise_for_status()
    return response.json()


def adjustment(invoice_id, kind, event, logical, amount, revision=1, corrects=None):
    return {
        "schema_version": "financial-adjustment.v1",
        "event_id": event,
        "logical_id": logical,
        "tenant": "northstar",
        "entity": "US",
        "kind": kind,
        "invoice_id": invoice_id,
        "amount_cents": amount,
        "currency": "USD",
        "revision": revision,
        "corrects_event": corrects,
        "effective_date": "2026-01-15",
        "observed_source_at": "2026-02-15T12:00:00+00:00"
        if revision > 1
        else "2026-01-20T12:00:00+00:00",
        "reason": "Synthetic credit correction" if revision > 1 else "Synthetic service adjustment",
    }


def compare_models(left, right):
    for model in ("fct_invoices", "fct_adjustments", "mart_reconciliation", "mart_period"):
        with (
            duckdb.connect(str(left), read_only=True) as a,
            duckdb.connect(str(right), read_only=True) as b,
        ):
            x = a.execute('SELECT * FROM "' + model + '" ORDER BY ALL').fetchall()
            y = b.execute('SELECT * FROM "' + model + '" ORDER BY ALL').fetchall()
            if x != y:
                raise AssertionError("independent_full_model_mismatch:" + model)


def execute_finance(stack, workspace):
    workspace = Path(workspace)
    controller = Controller(stack.config)
    initial = execute(stack)
    snapshot = extract(controller, SCOPES)
    database = workspace / "warehouse.duckdb"
    load(database, snapshot)
    baseline, baseline_dbt = build(database, workspace / "baseline-dbt")
    assert baseline == oracle(snapshot)
    assert baseline_dbt["period_rows"] == oracle_period(snapshot)
    first_release = publish(workspace / "releases", snapshot, baseline, extract(controller, SCOPES))
    invoice_id = next(r["invoice_id"] for r in baseline if r["entity"] == "US")
    receipts = []
    for source, kind, prefix in [
        ("stripe", "credit", "billing"),
        ("netsuite", "credit_posting", "finance"),
    ]:
        one = adjustment(invoice_id, kind, prefix + "-credit-v1", prefix + "-credit", 100000)
        receipts.append(inject(stack, source, one))
        receipts.append(inject(stack, source, one))  # Exact duplicate changes no financial record.
        two = adjustment(
            invoice_id, kind, prefix + "-credit-v2", prefix + "-credit", 200000, 2, one["event_id"]
        )
        receipts.append(inject(stack, source, two))
    receipts.append(
        inject(
            stack,
            "stripe",
            adjustment(
                invoice_id, "proration", "billing-proration-v1", "billing-proration", 150000
            ),
        )
    )
    corrected = extract(controller, SCOPES)
    load(database, corrected)
    rows, corrected_dbt = build(database, workspace / "corrected-dbt")
    independent = workspace / "independent.duckdb"
    load(independent, corrected)
    independent_rows, independent_dbt = build(independent, workspace / "independent-dbt")
    assert independent_rows == rows == oracle(corrected)
    assert (
        corrected_dbt["period_rows"] == independent_dbt["period_rows"] == oracle_period(corrected)
    )
    compare_models(database, independent)
    final = publish(workspace / "releases", corrected, rows, extract(controller, SCOPES))
    assert sum(r["billing_net_cents"] for r in rows) == 4150000
    assert sum(r["erp_net_cents"] for r in rows) == 4000000
    assert sum(r["allocated_cash_cents"] for r in rows) == 1650000
    return {
        "lane": "local_postgresql_http_duckdb_dbt",
        "initial_transaction_source_hash": initial["source_events_hash"],
        "original_release": first_release,
        "final_release": final,
        "source_snapshot": corrected,
        "source_snapshot_hash": digest(corrected),
        "source_event_receipts": receipts,
        "baseline_dbt": baseline_dbt,
        "corrected_dbt": corrected_dbt,
        "independent_dbt": independent_dbt,
        "all_four_models_exact_vs_independent_full": True,
        "materialization": "full table; no incremental performance claim",
        "native_vendor_execution": False,
        "independent_final_audit": "pending",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    target = Path(args.output)
    if target.exists():
        raise ValueError("fresh_evidence_path_required")
    with LocalStack(args.workspace) as stack:
        result = execute_finance(stack, stack.workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(
        json.dumps(
            {
                "models": result["corrected_dbt"]["models"],
                "tests": result["corrected_dbt"]["tests"],
                "release_state": result["final_release"]["state"],
                "invoice_rows": len(result["final_release"]["rows"]),
            }
        )
    )


if __name__ == "__main__":
    main()
