"""Full HTTP source snapshots, exact independent financial oracle, dbt gates and safe release."""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import yaml

from .contracts import Adjustment, digest
from .control import Refused
from .db import connect

ROOT = Path(__file__).resolve().parents[2]
PERIOD = {
    "start": "2026-01-01",
    "end_exclusive": "2026-04-01",
    "known_at": "2026-03-31T23:59:59+00:00",
}


def extract(controller, scopes):
    snapshots = []
    for tenant, entity in scopes:
        for source in ("salesforce", "stripe", "netsuite"):
            with controller.client(source) as client:
                response = client.get("/export", params={"tenant": tenant, "entity": entity})
                response.raise_for_status()
                snapshots.append(response.json())
    with connect(controller.dsn) as c:
        mappings = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM mappings ORDER BY tenant,entity,canonical_account,source,source_id"
            )
            if (r["tenant"], r["entity"]) in scopes
        ]
    return {
        "schema_version": "financial-snapshot.v1",
        "scopes": [list(s) for s in scopes],
        "period": PERIOD,
        "snapshots": snapshots,
        "mappings": mappings,
    }


def checked(snapshot):
    if snapshot["schema_version"] != "financial-snapshot.v1" or snapshot["period"] != PERIOD:
        raise Refused("unsupported_financial_contract")
    expected = {
        (t, e, s) for t, e in snapshot["scopes"] for s in ("salesforce", "stripe", "netsuite")
    }
    actual = [(x["tenant"], x["entity"], x["service"]) for x in snapshot["snapshots"]]
    if len(actual) != len(expected) or set(actual) != expected:
        raise Refused("source_coverage_gap")
    records, commitments, events = [], [], []
    for export in snapshot["snapshots"]:
        if export["complete_local_snapshot"] is not True or export["content_hash"] != digest(
            export["contents"]
        ):
            raise Refused("incomplete_or_changed_snapshot")
        source, tenant, entity = export["service"], export["tenant"], export["entity"]
        if source == "salesforce":
            for row in export["contents"]["contracts"]:
                if (row["tenant"], row["entity"], row["currency"]) != (tenant, entity, "USD"):
                    raise Refused("commercial_scope_or_currency_mismatch")
                commitments.append(row)
        else:
            for row in export["contents"]["records"]:
                if (row["tenant"], row["entity"], row["currency"]) != (tenant, entity, "USD"):
                    raise Refused("record_scope_or_currency_mismatch")
                records.append({**row, "source": source})
            for row in export["contents"]["source_events"]:
                if (row["tenant"], row["entity"], row["currency"]) != (tenant, entity, "USD"):
                    raise Refused("adjustment_scope_or_currency_mismatch")
                events.append({"source": source, **row})
    invoices = {r["id"]: r for r in records if r["source"] == "stripe" and r["kind"] == "invoice"}
    for event in events:
        Adjustment.model_validate({k: v for k, v in event.items() if k != "source"})
        allowed = (
            {"credit", "proration"}
            if event["source"] == "stripe"
            else {"credit_posting", "proration_posting"}
        )
        invoice = invoices.get(event["invoice_id"])
        if (
            event["kind"] not in allowed
            or not invoice
            or (invoice["tenant"], invoice["entity"]) != (event["tenant"], event["entity"])
        ):
            raise Refused("adjustment_reference_or_authority_mismatch")
    for rows, identity in [
        (records, lambda r: (r["source"], r["kind"], r["id"])),
        (commitments, lambda r: (r["tenant"], r["entity"], r["contract_id"])),
        (events, lambda r: (r["source"], r["event_id"])),
    ]:
        keys = [identity(r) for r in rows]
        if len(keys) != len(set(keys)):
            raise Refused("duplicate_source_identity")
    for row in records:
        for name in ("amount_cents", "monthly_cents", "unit_monthly_cents", "quantity"):
            if name in row and (type(row[name]) is not int or row[name] < 0):
                raise Refused("invalid_exact_money_or_quantity")
    by_key = {(r["source"], r["kind"], r["id"]): r for r in records}
    for row in records:
        allowed = (
            {
                "subscription",
                "subscription_item",
                "invoice",
                "invoice_line",
                "payment",
                "payment_allocation",
            }
            if row["source"] == "stripe"
            else {"posting", "payment_application"}
        )
        if row["kind"] not in allowed:
            raise Refused("unsupported_source_record_kind")
        if row.get("invoice_id"):
            invoice = invoices.get(row["invoice_id"])
            if not invoice or (row["tenant"], row["entity"], row["canonical_account"]) != (
                invoice["tenant"],
                invoice["entity"],
                invoice["canonical_account"],
            ):
                raise Refused("orphan_or_cross_scope_financial_reference")
        if row["kind"] == "payment_application":
            allocation = by_key.get(("stripe", "payment_allocation", row["allocation_id"]))
            if not allocation or allocation["invoice_id"] != row["invoice_id"]:
                raise Refused("payment_application_lineage_missing")
    heads = {}
    for event in sorted(events, key=lambda x: x["revision"]):
        identity = (event["source"], event["tenant"], event["entity"], event["logical_id"])
        old = heads.get(identity)
        if (old is None and event["revision"] != 1) or (
            old is not None
            and (
                event["revision"] != old["revision"] + 1
                or event["corrects_event"] != old["event_id"]
                or (event["kind"], event["invoice_id"]) != (old["kind"], old["invoice_id"])
            )
        ):
            raise Refused("source_correction_coverage_gap")
        heads[identity] = event
    return commitments, records, events


def oracle(snapshot):
    """Derives values from raw service records; never reads dbt tables or shared SQL formulas."""
    commitments, records, events = checked(snapshot)
    by_id = {(r["source"], r["kind"], r["id"]): r for r in records}
    contracts = {(r["tenant"], r["entity"], r["contract_id"]): r for r in commitments}
    # Full correction chains were checked above; select latest known revision independently.
    heads = {}
    for event in events:
        if datetime.fromisoformat(event["observed_source_at"]) <= datetime.fromisoformat(
            PERIOD["known_at"]
        ):
            identity = (event["source"], event["tenant"], event["entity"], event["logical_id"])
            if identity not in heads or heads[identity]["revision"] < event["revision"]:
                heads[identity] = event
    allocations = [
        r for r in records if r["source"] == "stripe" and r["kind"] == "payment_allocation"
    ]
    for payment in [r for r in records if r["source"] == "stripe" and r["kind"] == "payment"]:
        linked = [r for r in allocations if r["payment_id"] == payment["id"]]
        if sum(r["amount_cents"] for r in linked) > payment["amount_cents"] or any(
            (r["tenant"], r["entity"], r["customer"], r["payment_date"])
            != (payment["tenant"], payment["entity"], payment["customer"], payment["payment_date"])
            for r in linked
        ):
            raise Refused("payment_allocation_lineage_or_total_mismatch")
    result = []
    for invoice in [r for r in records if r["source"] == "stripe" and r["kind"] == "invoice"]:
        if not PERIOD["start"] <= invoice["invoice_date"] < PERIOD["end_exclusive"]:
            continue
        scope = (invoice["tenant"], invoice["entity"])
        subscription = by_id[("stripe", "subscription", invoice["subscription_id"])]
        contract = contracts[(*scope, subscription["contract_id"])]
        if (subscription["tenant"], subscription["entity"], subscription["canonical_account"]) != (
            *scope,
            invoice["canonical_account"],
        ):
            raise Refused("invoice_subscription_scope_mismatch")
        lines = [
            r
            for r in records
            if r["source"] == "stripe"
            and r["kind"] == "invoice_line"
            and r["invoice_id"] == invoice["id"]
        ]
        if not lines or sum(r["amount_cents"] for r in lines) != invoice["amount_cents"]:
            raise Refused("invoice_header_line_variance")
        for line in lines:
            item = by_id[("stripe", "subscription_item", line["item_id"])]
            if (line["tenant"], line["entity"], item["subscription_id"]) != (
                *scope,
                subscription["id"],
            ):
                raise Refused("invoice_line_scope_mismatch")
        related = [r for r in records if r.get("invoice_id") == invoice["id"]]
        if any(
            (r["tenant"], r["entity"], r["canonical_account"])
            != (*scope, invoice["canonical_account"])
            for r in related
        ):
            raise Refused("cross_scope_financial_reference")
        adjustments = [
            e
            for e in heads.values()
            if e["invoice_id"] == invoice["id"]
            and PERIOD["start"] <= e["effective_date"] < PERIOD["end_exclusive"]
        ]
        if any((e["tenant"], e["entity"]) != scope for e in adjustments):
            raise Refused("cross_scope_adjustment")
        credit = sum(
            e["amount_cents"]
            for e in adjustments
            if e["source"] == "stripe" and e["kind"] == "credit"
        )
        proration = sum(
            e["amount_cents"]
            for e in adjustments
            if e["source"] == "stripe" and e["kind"] == "proration"
        )
        erp_adjustment = sum(
            e["amount_cents"] * (-1 if e["kind"] == "credit_posting" else 1)
            for e in adjustments
            if e["source"] == "netsuite"
        )
        cash = sum(
            r["amount_cents"]
            for r in related
            if r["source"] == "stripe"
            and r["kind"] == "payment_allocation"
            and PERIOD["start"] <= r["payment_date"] < PERIOD["end_exclusive"]
        )
        applications = sum(
            r["amount_cents"]
            for r in related
            if r["source"] == "netsuite"
            and r["kind"] == "payment_application"
            and PERIOD["start"] <= r["posting_date"] < PERIOD["end_exclusive"]
        )
        posting = sum(
            r["amount_cents"]
            for r in related
            if r["source"] == "netsuite"
            and r["kind"] == "posting"
            and PERIOD["start"] <= r["posting_date"] < PERIOD["end_exclusive"]
        )
        expected = contract["unit_monthly_cents"] * contract["quantity"] * 3 + proration - credit
        net = invoice["amount_cents"] + proration - credit
        if credit > invoice["amount_cents"] + proration or cash > net:
            raise Refused("credit_or_cash_exceeds_net_invoice")
        for allocation in [
            r for r in related if r["source"] == "stripe" and r["kind"] == "payment_allocation"
        ]:
            payment = by_id[("stripe", "payment", allocation["payment_id"])]
            if (
                payment["customer"] != invoice["customer"]
                or allocation["amount_cents"] > payment["amount_cents"]
            ):
                raise Refused("invalid_payment_source_lineage")
        result.append(
            {
                "invoice_id": invoice["id"],
                "tenant": scope[0],
                "entity": scope[1],
                "currency": "USD",
                "expected_invoice_cents": expected,
                "billing_net_cents": net,
                "erp_net_cents": posting + erp_adjustment,
                "allocated_cash_cents": cash,
                "erp_applied_cash_cents": applications,
                "credit_cents": credit,
                "proration_cents": proration,
                "billing_contract_variance_cents": net - expected,
                "posting_variance_cents": posting + erp_adjustment - net,
                "cash_application_variance_cents": applications - cash,
            }
        )
    return sorted(result, key=lambda x: x["invoice_id"])


def load(database, snapshot):
    commitments, records, events = checked(snapshot)
    with duckdb.connect(str(database)) as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS raw")
        for name, rows in (
            ("commitments", commitments),
            ("records", records),
            ("adjustments", events),
        ):
            c.execute(f"CREATE OR REPLACE TABLE raw.{name}(body JSON)")
            if rows:
                c.executemany(
                    f"INSERT INTO raw.{name} VALUES (?)", [(json.dumps(r),) for r in rows]
                )


def build(database, workspace):
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    profiles = {
        "integrity": {
            "target": "local",
            "outputs": {
                "local": {"type": "duckdb", "path": str(Path(database).resolve()), "threads": 1}
            },
        }
    }
    (workspace / "profiles.yml").write_text(yaml.safe_dump(profiles))
    environment = {**os.environ, "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DO_NOT_TRACK": "true"}
    with (workspace / "dbt.log").open("w") as log:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "dbt.cli.main",
                "build",
                "--project-dir",
                str(ROOT / "dbt"),
                "--profiles-dir",
                str(workspace),
                "--target-path",
                str(workspace / "target"),
                "--log-path",
                str(workspace / "logs"),
                "--no-use-colors",
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    if result.returncode:
        raise Refused("dbt_financial_gate_failed")
    results = json.loads((workspace / "target/run_results.json").read_text())
    manifest = json.loads((workspace / "target/manifest.json").read_text())
    models = [r for r in results["results"] if r["unique_id"].startswith("model.")]
    tests = [r for r in results["results"] if r["unique_id"].startswith("test.")]
    expected_tests = {k for k, v in manifest["nodes"].items() if v["resource_type"] == "test"}
    if (
        len(models) != 4
        or len(tests) < 15
        or {r["unique_id"] for r in tests} != expected_tests
        or any(r["status"] not in ("success", "pass") for r in results["results"])
    ):
        raise Refused("dbt_result_coverage_failed")
    with duckdb.connect(str(database), read_only=True) as c:
        cursor = c.execute("SELECT * FROM mart_reconciliation ORDER BY invoice_id")
        rows = [dict(zip([x[0] for x in cursor.description], row)) for row in cursor.fetchall()]
        period_cursor = c.execute("SELECT * FROM mart_period ORDER BY tenant,entity")
        period_rows = [
            dict(zip([x[0] for x in period_cursor.description], r))
            for r in period_cursor.fetchall()
        ]
    return rows, {
        "models": len(models),
        "tests": len(tests),
        "period_rows": period_rows,
        "results": [{"id": r["unique_id"], "status": r["status"]} for r in results["results"]],
    }


def publish(root, snapshot, rows, fresh_snapshot, fail_before_pointer=False):
    expected = oracle(snapshot)
    if expected != rows:
        raise Refused("independent_financial_parity_failed")
    if snapshot != fresh_snapshot:
        raise Refused("source_changed_during_reconciliation")
    result = {
        "contract": "reconciled-release.v1",
        "period": PERIOD,
        "source_hash": digest(snapshot),
        "rows": rows,
        "comparison_scope": "issued Q1 invoices; missing commitments reported separately",
        "unbilled_commitments": coverage(snapshot),
        "period_measures": oracle_period(snapshot),
        "variance_bridge": bridge(snapshot, rows),
        "state": "review_required"
        if coverage(snapshot)
        or any(
            r["posting_variance_cents"]
            or r["cash_application_variance_cents"]
            or r["billing_contract_variance_cents"]
            for r in rows
        )
        else "reconciled",
    }
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    release = digest(result)
    destination = root / (release + ".json")
    if destination.exists() and destination.read_bytes() != data:
        raise Refused("release_identity_content_conflict")
    if not destination.exists():
        temporary_release = root / (release + ".pending")
        temporary_release.write_bytes(data)
        os.replace(temporary_release, destination)
    if fail_before_pointer:
        raise Refused("controlled_publication_interruption")
    temporary = root / "CURRENT.pending"
    temporary.write_bytes(
        (json.dumps({"release": release, "file": destination.name}) + "\n").encode()
    )
    os.replace(temporary, root / "CURRENT.json")
    return result


def coverage(snapshot):
    commitments, records, _ = checked(snapshot)
    activated = {
        (r["tenant"], r["entity"], r["contract_id"])
        for r in records
        if r["source"] == "stripe" and r["kind"] == "subscription"
    }
    result = []
    for commitment in commitments:
        scope = (commitment["tenant"], commitment["entity"])
        if (*scope, commitment["contract_id"]) not in activated:
            maps = [
                r
                for r in snapshot["mappings"]
                if (r["tenant"], r["entity"], r["source"], r["source_id"], r["status"])
                == (*scope, "salesforce", commitment["account_id"], "approved")
            ]
            result.append(
                {
                    "tenant": scope[0],
                    "entity": scope[1],
                    "contract_id": commitment["contract_id"],
                    "reason": "ambiguous_or_missing_mapping"
                    if len(maps) != 1
                    else "activation_lag_requires_review",
                    "scheduled_quarter_cents": commitment["unit_monthly_cents"]
                    * commitment["quantity"]
                    * 3,
                }
            )
    return sorted(result, key=lambda x: (x["tenant"], x["entity"], x["contract_id"]))


def oracle_period(snapshot):
    _, records, _ = checked(snapshot)
    subscriptions = {
        r["id"]: r for r in records if r["source"] == "stripe" and r["kind"] == "subscription"
    }
    result = {}
    for row in oracle(snapshot):
        scope = (row["tenant"], row["entity"])
        target = result.setdefault(
            scope,
            {
                "tenant": scope[0],
                "entity": scope[1],
                "currency": "USD",
                "expected_invoice_cents": 0,
                "billing_net_cents": 0,
                "erp_net_cents": 0,
                "allocated_cash_cents": 0,
                "period_end_arr_cents": 0,
            },
        )
        for metric in (
            "expected_invoice_cents",
            "billing_net_cents",
            "erp_net_cents",
            "allocated_cash_cents",
        ):
            target[metric] += row[metric]
    for item in [
        r for r in records if r["source"] == "stripe" and r["kind"] == "subscription_item"
    ]:
        subscription = subscriptions[item["subscription_id"]]
        scope = (item["tenant"], item["entity"])
        if (
            subscription["effective_start"] <= "2026-03-31" < subscription["effective_end"]
            and subscription["status"] == "active"
            and scope in result
        ):
            result[scope]["period_end_arr_cents"] += (
                item["unit_monthly_cents"] * item["quantity"] * 12
            )
    return [result[s] for s in sorted(result)]


def bridge(snapshot, rows):
    _, records, events = checked(snapshot)
    entries = []
    for row in rows:
        invoice_id = row["invoice_id"]
        source_ids = [
            r["id"] for r in records if r.get("invoice_id") == invoice_id or r["id"] == invoice_id
        ]
        common = {
            "invoice_id": invoice_id,
            "tenant": row["tenant"],
            "entity": row["entity"],
            "currency": "USD",
            "source_record_ids": sorted(source_ids),
        }
        for category, amount, owner, note in (
            (
                "credit",
                -row["credit_cents"],
                "billing",
                "Latest known credit revision reduces the invoice cohort",
            ),
            (
                "proration",
                row["proration_cents"],
                "billing",
                "Explicit source charge; not inferred recurring ARR",
            ),
            (
                "contract_mismatch",
                row["billing_contract_variance_cents"],
                "commercial_operations",
                "Review contract-to-invoice basis",
            ),
            (
                "unposted_adjustment_requires_review",
                row["posting_variance_cents"],
                "finance",
                "Possible timing or missing posting; no assertion of benign lag without review",
            ),
            (
                "unsettled_invoice_balance",
                row["billing_net_cents"] - row["allocated_cash_cents"],
                "accounts_receivable",
                "Issued invoice cohort less matched allocations; not revenue uplift",
            ),
            (
                "cash_application_mismatch",
                row["cash_application_variance_cents"],
                "finance",
                "Compare payment allocation with ERP application",
            ),
        ):
            if amount:
                entries.append(
                    {
                        **common,
                        "category": category,
                        "amount_cents": amount,
                        "owner": owner,
                        "explanation": note,
                    }
                )
    corrections = [
        {
            "source": e["source"],
            "event_id": e["event_id"],
            "corrects_event": e["corrects_event"],
            "revision": e["revision"],
            "effective_date": e["effective_date"],
            "observed_source_at": e["observed_source_at"],
        }
        for e in events
        if e["revision"] > 1
    ]
    return {
        "scope": "Q1 issued invoices; ARR shown separately",
        "entries": entries,
        "backdated_correction_lineage": corrections,
        "unbilled_commitments": coverage(snapshot),
    }
