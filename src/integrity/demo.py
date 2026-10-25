import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .control import Controller
from .contracts import digest
from .db import connect
from .harness import LocalStack


def snapshot(stack):
    result = {}
    for name, dsn in stack.admins.items():
        with connect(dsn) as c:
            tables = (
                ("inbox", "outbox", "attempts", "exceptions", "audit")
                if name == "control"
                else (
                    ("events", "contracts")
                    if name == "salesforce"
                    else ("records", "effects", "requests")
                )
            )
            result[name] = {
                table: c.execute("SELECT * FROM " + table).fetchall() for table in tables
            }
    # Only fixture records/operational observation timestamps; no DSNs or credentials.
    return json.loads(json.dumps(result, default=str))


def execute(stack):
    controller = Controller(stack.config)
    for event in stack.events[:2]:
        assert controller.ingest(event)["status"] == "planned"
    with connect(stack.admins["control"]) as c:
        first = c.execute("SELECT * FROM outbox WHERE entity='US'").fetchone()
    service = stack.config["services"]["stripe"]
    response = httpx.post(
        service["url"] + "/test/faults",
        headers={"Authorization": "Bearer " + stack.injector},
        json={"effect_id": str(first["effect_id"]), "modes": ["lost_ack"]},
        trust_env=False,
    )
    response.raise_for_status()
    assert controller.step(first["effect_id"], timeout=0.25) == "uncertain"
    with connect(stack.admins["stripe"]) as c:
        remote_committed = c.execute(
            "SELECT count(*) AS n FROM effects WHERE effect_id=%s", (first["effect_id"],)
        ).fetchone()["n"]
    assert remote_committed == 1
    assert controller.step(first["effect_id"]) == "verified"
    controller.drain()
    duplicate = controller.ingest(stack.events[0])
    ambiguous = controller.ingest(stack.events[2])
    state = snapshot(stack)
    assert len(state["stripe"]["effects"]) == 2 and len(state["netsuite"]["effects"]) == 2
    finance = [r["body"] for r in state["netsuite"]["records"]]
    invoices = [r["body"] for r in state["stripe"]["records"] if r["kind"] == "invoice"]
    totals = {
        "currency": "USD",
        "quarterly_invoice_cents": sum(r["amount_cents"] for r in invoices),
        "erp_invoice_posting_cents": sum(
            r["amount_cents"] for r in finance if r["kind"] == "posting"
        ),
        "allocated_cash_cents": sum(
            r["amount_cents"] for r in finance if r["kind"] == "payment_application"
        ),
    }
    assert totals["quarterly_invoice_cents"] == totals["erp_invoice_posting_cents"] == 4200000
    assert totals["allocated_cash_cents"] == 1650000
    return {
        "lane": "local_synthetic_contract_acceptance",
        "native_vendor_calls": 0,
        "executed_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "postgresql": server_version(stack),
        "platform": platform.system(),
        "source_events": stack.events,
        "source_events_hash": digest(stack.events),
        "lost_ack": {"remote_committed_before_recovery": remote_committed, "recovered": True},
        "duplicate_event": duplicate,
        "ambiguous_mapping": ambiguous,
        "totals": totals,
        "persisted_state": state,
    }


def server_version(stack):
    with connect(stack.config["control_dsn"]) as c:
        return c.execute("SELECT current_setting('server_version') AS version").fetchone()[
            "version"
        ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise ValueError("new_evidence_path_required")
    with LocalStack(args.workspace) as stack:
        result = execute(stack)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "evidence": str(output),
                **result["totals"],
                "lost_ack": result["lost_ack"],
                "ambiguity": result["ambiguous_mapping"]["detail"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
