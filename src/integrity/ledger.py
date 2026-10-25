"""Persist scoped financial observations only after independent source parity succeeds."""

from psycopg.types.json import Jsonb

from .contracts import digest, key
from .control import Refused
from .db import connect, lock
from .finance import oracle

ACTIONABLE = {
    "activation_lag",
    "contract_mismatch",
    "unposted_adjustment_requires_review",
    "unmapped_legal_entity",
    "cash_application_mismatch",
}


def record(controller, snapshot, release):
    if release["source_hash"] != digest(snapshot) or release["rows"] != oracle(snapshot):
        raise Refused("financial_ledger_requires_verified_source_parity")
    results = []
    for tenant, entity in snapshot["scopes"]:
        report = {
            "period": release["period"],
            "rows": [r for r in release["rows"] if (r["tenant"], r["entity"]) == (tenant, entity)],
            "period_measures": [
                r
                for r in release["period_measures"]
                if (r["tenant"], r["entity"]) == (tenant, entity)
            ],
            "unbilled_commitments": [
                r
                for r in release["unbilled_commitments"]
                if (r["tenant"], r["entity"]) == (tenant, entity)
            ],
            "entries": [
                r
                for r in release["variance_bridge"]["entries"]
                if (r.get("tenant"), r.get("entity")) == (tenant, entity)
            ],
        }
        run = key("financial-run", tenant, entity, digest(report), release["source_hash"])
        with connect(controller.dsn) as c:
            lock(c, "financial-ledger", tenant, entity)
            previous = c.execute("SELECT report FROM financial_runs WHERE id=%s", (run,)).fetchone()
            if previous:
                if previous["report"] != report:
                    raise Refused("financial_run_content_conflict")
                results.append(run)
                continue
            c.execute(
                "INSERT INTO financial_runs(id,tenant,entity,source_hash,period,report) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    run,
                    tenant,
                    entity,
                    release["source_hash"],
                    Jsonb(release["period"]),
                    Jsonb(report),
                ),
            )
            seen = []
            for entry in report["entries"]:
                identity = key(
                    "financial-case",
                    tenant,
                    entity,
                    entry["category"],
                    entry.get("invoice_id", entry.get("contract_id")),
                    release["period"]["start"],
                )
                state = "open" if entry["category"] in ACTIONABLE else "explained"
                c.execute(
                    "INSERT INTO financial_cases(id,tenant,entity,category,owner,state,amount_cents,last_run,detail) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT(id) DO UPDATE SET state=excluded.state,amount_cents=excluded.amount_cents,last_run=excluded.last_run,detail=excluded.detail,updated_at=clock_timestamp()",
                    (
                        identity,
                        tenant,
                        entity,
                        entry["category"],
                        entry["owner"],
                        state,
                        entry.get("amount_cents"),
                        run,
                        Jsonb(entry),
                    ),
                )
                c.execute(
                    "INSERT INTO financial_case_events(tenant,entity,case_id,run_id,state,detail) VALUES(%s,%s,%s,%s,%s,%s)",
                    (tenant, entity, identity, run, state, Jsonb(entry)),
                )
                seen.append(identity)
            old = c.execute(
                "SELECT fc.* FROM financial_cases fc JOIN financial_runs fr ON fr.id=fc.last_run WHERE fc.tenant=%s AND fc.entity=%s AND fc.state='open' AND fr.period->>'start'=%s",
                (tenant, entity, release["period"]["start"]),
            ).fetchall()
            for case in old:
                if str(case["id"]) not in seen:
                    c.execute(
                        "UPDATE financial_cases SET state='reconciled',last_run=%s WHERE id=%s",
                        (run, case["id"]),
                    )
                    c.execute(
                        "INSERT INTO financial_case_events(tenant,entity,case_id,run_id,state,detail) VALUES(%s,%s,%s,%s,'reconciled',%s)",
                        (
                            tenant,
                            entity,
                            case["id"],
                            run,
                            Jsonb({"reason": "absent_from_new_verified_source_comparison"}),
                        ),
                    )
            controller.audit(
                c,
                {"tenant": tenant, "entity": entity},
                "financial_comparison",
                run,
                {"source_hash": release["source_hash"], "cases": len(seen)},
            )
        results.append(run)
    return results
