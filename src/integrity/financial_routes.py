"""Finite local source-event operations. Injector represents synthetic upstream sources, not an operator."""

from fastapi import Depends, HTTPException
from psycopg.types.json import Jsonb

from .contracts import Adjustment, digest
from .db import connect, lock


def mount(app, kind, dsn, authorized, injection):
    @app.get("/export", dependencies=[Depends(authorized)])
    def export(tenant: str, entity: str):
        tables = (
            ("accounts", "contracts", "events", "opportunities")
            if kind == "salesforce"
            else ("records", "source_events")
        )
        with connect(dsn) as c:
            # connect() has SET statement_timeout but no source read before this isolation setting.
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            contents = {
                table: [
                    r["body"]
                    for r in c.execute(
                        f"SELECT body FROM {table} WHERE tenant=%s AND entity=%s ORDER BY body::text",
                        (tenant, entity),
                    )
                ]
                for table in tables
            }
        return {
            "service": kind,
            "tenant": tenant,
            "entity": entity,
            "complete_local_snapshot": True,
            "contents": contents,
            "content_hash": digest(contents),
        }

    if kind == "salesforce":
        return

    @app.post("/test/source-events", dependencies=[Depends(injection)])
    def source_event(body: Adjustment):
        allowed = (
            {"credit", "proration"} if kind == "stripe" else {"credit_posting", "proration_posting"}
        )
        if body.kind not in allowed:
            raise HTTPException(403, "source_domain_not_owned")
        document = body.model_dump(mode="json")
        with connect(dsn) as c:
            lock(c, body.tenant, body.entity, body.invoice_id)
            lock(c, body.tenant, body.entity, body.logical_id)
            previous = c.execute(
                "SELECT body FROM source_events WHERE event_id=%s", (body.event_id,)
            ).fetchone()
            if previous:
                if previous["body"] != document:
                    raise HTTPException(409, "source_event_identity_content_conflict")
                return {"event_id": body.event_id, "duplicate": True, "hash": digest(document)}
            head = c.execute(
                "SELECT body FROM source_events WHERE tenant=%s AND entity=%s AND logical_id=%s "
                "ORDER BY revision DESC LIMIT 1",
                (body.tenant, body.entity, body.logical_id),
            ).fetchone()
            if head:
                old = head["body"]
                if (
                    body.revision != old["revision"] + 1
                    or body.corrects_event != old["event_id"]
                    or (body.kind, body.invoice_id, body.currency)
                    != (old["kind"], old["invoice_id"], old["currency"])
                ):
                    raise HTTPException(409, "stale_or_invalid_correction_chain")
            elif body.revision != 1:
                raise HTTPException(409, "missing_correction_history")
            refkind = "invoice" if kind == "stripe" else "posting"
            reference = c.execute(
                "SELECT body FROM records WHERE kind=%s AND tenant=%s AND entity=%s "
                "AND " + ("id=%s" if kind == "stripe" else "body->>'invoice_id'=%s"),
                (refkind, body.tenant, body.entity, body.invoice_id),
            ).fetchone()
            if not reference:
                raise HTTPException(409, "invoice_not_found_in_scope")
            if (
                body.kind in ("credit", "credit_posting")
                and body.amount_cents > reference["body"]["amount_cents"]
            ):
                raise HTTPException(422, "credit_exceeds_invoice")
            if body.kind in ("credit", "credit_posting"):
                all_events = c.execute(
                    "SELECT DISTINCT ON(logical_id) logical_id,body FROM source_events "
                    "WHERE tenant=%s AND entity=%s AND body->>'invoice_id'=%s ORDER BY logical_id,revision DESC",
                    (body.tenant, body.entity, body.invoice_id),
                ).fetchall()
                other = sum(
                    r["body"]["amount_cents"]
                    for r in all_events
                    if r["logical_id"] != body.logical_id and r["body"]["kind"] == body.kind
                )
                if other + body.amount_cents > reference["body"]["amount_cents"]:
                    raise HTTPException(422, "aggregate_credit_exceeds_invoice")
            c.execute(
                "INSERT INTO source_events(event_id,tenant,entity,logical_id,revision,body) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    body.event_id,
                    body.tenant,
                    body.entity,
                    body.logical_id,
                    body.revision,
                    Jsonb(document),
                ),
            )
        return {"event_id": body.event_id, "duplicate": False, "hash": digest(document)}
