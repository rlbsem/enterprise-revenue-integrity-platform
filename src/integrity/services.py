"""Three actual HTTP processes; each opens only its own PostgreSQL database."""

import os
import secrets
import time
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from .contracts import Commercial, Effect, Fault, digest, key
from .db import connect, lock
from .financial_routes import mount


def create_app(kind=None, dsn=None, token=None, injector=None):
    kind = kind or os.environ["SERVICE_KIND"]
    dsn = dsn or os.environ["SERVICE_DSN"]
    token = token or os.environ["SERVICE_TOKEN"]
    injector = injector or os.environ["INJECTOR_TOKEN"]
    app = FastAPI(title=f"Local {kind} contract service — synthetic only")

    def authorized(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "service_identity_required")

    def injection(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, "Bearer " + injector):
            raise HTTPException(401, "test_injector_required")

    @app.get("/health")
    def health():
        with connect(dsn) as c:
            version = c.execute("SELECT max(version) AS version FROM schema_version").fetchone()[
                "version"
            ]
        return {"status": "ready", "service": kind, "schema_version": version, "native": False}

    mount(app, kind, dsn, authorized, injection)

    if kind == "salesforce":

        @app.get("/events/{record_id}", dependencies=[Depends(authorized)])
        def event(record_id: str, tenant: str, entity: str):
            return source_read("events", record_id, tenant, entity)

        @app.get("/opportunities/{record_id}", dependencies=[Depends(authorized)])
        def opportunity(record_id: str, tenant: str, entity: str):
            return source_read("opportunities", record_id, tenant, entity)

        @app.get("/contracts/{record_id}", dependencies=[Depends(authorized)])
        def contract(record_id: str, tenant: str, entity: str):
            return source_read("contracts", record_id, tenant, entity)

        def source_read(table, record_id, tenant, entity):
            # table is selected only by the fixed route definitions above.
            with connect(dsn) as c:
                row = c.execute(
                    f"SELECT body FROM {table} WHERE id=%s AND tenant=%s AND entity=%s",
                    (record_id, tenant, entity),
                ).fetchone()
            if not row:
                raise HTTPException(404, "not_found_in_scope")
            return row["body"]

        return app

    @app.post("/test/faults", dependencies=[Depends(injection)])
    def faults(body: Fault):
        with connect(dsn) as c:
            c.execute(
                "INSERT INTO faults VALUES (%s,%s) ON CONFLICT(effect_id) "
                "DO UPDATE SET modes=excluded.modes",
                (body.effect_id, Jsonb(body.modes)),
            )
        return {"configured": True}

    @app.get("/receipts/{effect_id}", dependencies=[Depends(authorized)])
    def receipt(effect_id: UUID, tenant: str, entity: str):
        with connect(dsn) as c:
            row = c.execute(
                "SELECT receipt FROM effects WHERE effect_id=%s AND tenant=%s AND entity=%s",
                (effect_id, tenant, entity),
            ).fetchone()
            c.execute(
                "INSERT INTO requests(effect_id,operation,status) VALUES(%s,'readback',%s)",
                (effect_id, 200 if row else 404),
            )
        if not row:
            raise HTTPException(404, "not_committed_in_scope")
        return row["receipt"]

    @app.get("/records/{record_kind}/{record_id}", dependencies=[Depends(authorized)])
    def record(record_kind: str, record_id: str, tenant: str, entity: str):
        with connect(dsn) as c:
            row = c.execute(
                "SELECT body FROM records WHERE kind=%s AND id=%s AND tenant=%s AND entity=%s",
                (record_kind, record_id, tenant, entity),
            ).fetchone()
        if not row:
            raise HTTPException(404, "not_found_in_scope")
        return row["body"]

    @app.post("/effects", dependencies=[Depends(authorized)])
    def apply(body: Effect, idempotency_key: str = Header()):
        payload = body.model_dump(mode="json")
        if idempotency_key != body.effect_id:
            raise HTTPException(409, "effect_identity_mismatch")
        mode = "ok"
        with connect(dsn) as c:
            lock(c, body.effect_id)
            previous = c.execute(
                "SELECT * FROM effects WHERE effect_id=%s", (body.effect_id,)
            ).fetchone()
            if previous:
                if previous["hash"] != digest(payload) or previous["payload"] != payload:
                    raise HTTPException(409, "idempotency_exact_content_conflict")
                c.execute(
                    "INSERT INTO requests(effect_id,operation,status) VALUES(%s,'duplicate',200)",
                    (body.effect_id,),
                )
                return previous["receipt"]
            lock(c, body.tenant, body.entity, body.destination_customer)
            customer = c.execute(
                "SELECT * FROM customers WHERE tenant=%s AND entity=%s AND id=%s",
                (body.tenant, body.entity, body.destination_customer),
            ).fetchone()
            if not customer or customer["canonical_account"] != body.canonical_account:
                raise HTTPException(409, "receiver_customer_identity_mismatch")
            fault = c.execute(
                "SELECT modes FROM faults WHERE effect_id=%s FOR UPDATE", (body.effect_id,)
            ).fetchone()
            if fault and fault["modes"]:
                mode = fault["modes"][0]
                c.execute(
                    "UPDATE faults SET modes=%s WHERE effect_id=%s",
                    (Jsonb(fault["modes"][1:]), body.effect_id),
                )
            if mode not in ("unavailable", "reject"):
                try:
                    records = (
                        billing_records(c, body) if kind == "stripe" else finance_records(body)
                    )
                except (ValueError, KeyError, TypeError) as exc:
                    raise HTTPException(422, "invalid_business_contract") from exc
                for item in records:
                    c.execute(
                        "INSERT INTO records VALUES(%s,%s,%s,%s,%s)",
                        (item["kind"], item["id"], body.tenant, body.entity, Jsonb(item)),
                    )
                result = {
                    "effect_id": body.effect_id,
                    "payload_hash": digest(payload),
                    "payload": payload,
                    "records": records,
                    "records_hash": digest(records),
                    "receipt_id": key(kind, body.effect_id, "receipt"),
                    "service": kind,
                }
                c.execute(
                    "INSERT INTO effects(effect_id,tenant,entity,hash,payload,receipt) VALUES(%s,%s,%s,%s,%s,%s)",
                    (
                        body.effect_id,
                        body.tenant,
                        body.entity,
                        digest(payload),
                        Jsonb(payload),
                        Jsonb(result),
                    ),
                )
            c.execute(
                "INSERT INTO requests(effect_id,operation,status) VALUES(%s,'apply',%s)",
                (
                    body.effect_id,
                    503 if mode == "unavailable" else 422 if mode == "reject" else 201,
                ),
            )
        # The business records AND receipt have committed before the delayed acknowledgment.
        if mode == "lost_ack":
            time.sleep(1.5)
        if mode in ("unavailable", "reject"):
            return JSONResponse(
                status_code=503 if mode == "unavailable" else 422,
                content={"error": "controlled_" + mode},
            )
        return result

    return app


def base_record(effect, kind, suffix, **fields):
    return {
        "kind": kind,
        "id": key(effect.effect_id, suffix),
        "tenant": effect.tenant,
        "entity": effect.entity,
        "customer": effect.destination_customer,
        "canonical_account": effect.canonical_account,
        "source_event": effect.source_event,
        "source_revision": effect.source_revision,
        "currency": effect.currency,
        **fields,
    }


def billing_records(c, effect):
    if effect.operation != "activate_and_invoice":
        raise ValueError("operation_authority")
    commercial = Commercial.model_validate(effect.facts)
    if (
        commercial.stage != "Closed Won"
        or commercial.tenant != effect.tenant
        or commercial.entity != effect.entity
    ):
        raise ValueError("commercial_scope")
    if commercial.event_id != effect.source_event or commercial.revision != effect.source_revision:
        raise ValueError("source_revision")
    # Pass 1 supports a first activation, not a second full invoice for a revised contract.
    existing = c.execute(
        "SELECT 1 FROM records WHERE kind='subscription' AND tenant=%s AND entity=%s "
        "AND body->>'contract_id'=%s",
        (effect.tenant, effect.entity, commercial.contract_id),
    ).fetchone()
    if existing:
        raise ValueError("amendment_requires_separate_authorized_operation")
    monthly = commercial.unit_monthly_cents * commercial.quantity
    subscription = base_record(
        effect,
        "subscription",
        "subscription",
        contract_id=commercial.contract_id,
        effective_start=commercial.effective_start,
        effective_end=commercial.effective_end,
        billing_interval_months=3,
        status="active",
    )
    item = base_record(
        effect,
        "subscription_item",
        "item",
        subscription_id=subscription["id"],
        unit_monthly_cents=commercial.unit_monthly_cents,
        quantity=commercial.quantity,
        monthly_cents=monthly,
    )
    invoice = base_record(
        effect,
        "invoice",
        "invoice",
        subscription_id=subscription["id"],
        invoice_date=commercial.effective_start,
        amount_cents=monthly * 3,
        status="open",
    )
    line = base_record(
        effect,
        "invoice_line",
        "line",
        invoice_id=invoice["id"],
        item_id=item["id"],
        amount_cents=monthly * 3,
        service_months=3,
    )
    records = [subscription, item, invoice, line]
    payments = c.execute(
        "SELECT body FROM records WHERE kind='payment' AND tenant=%s AND entity=%s "
        "AND body->>'customer'=%s ORDER BY id",
        (effect.tenant, effect.entity, effect.destination_customer),
    ).fetchall()
    remaining = invoice["amount_cents"]
    for row in payments:
        payment = row["body"]
        used = c.execute(
            "SELECT coalesce(sum((body->>'amount_cents')::bigint),0) AS cents FROM records "
            "WHERE kind='payment_allocation' AND body->>'payment_id'=%s",
            (payment["id"],),
        ).fetchone()["cents"]
        available = min(remaining, payment["amount_cents"] - int(used))
        if available > 0:
            records.append(
                base_record(
                    effect,
                    "payment_allocation",
                    payment["id"],
                    payment_id=payment["id"],
                    invoice_id=invoice["id"],
                    amount_cents=available,
                    payment_date=payment["payment_date"],
                )
            )
            remaining -= available
    return records


def finance_records(effect):
    if effect.operation != "post_invoice_and_cash" or set(effect.facts) != {"billing_receipt"}:
        raise ValueError("operation_authority")
    receipt = effect.facts["billing_receipt"]
    payload = Effect.model_validate(receipt["payload"])
    if receipt["payload_hash"] != digest(receipt["payload"]) or receipt["records_hash"] != digest(
        receipt["records"]
    ):
        raise ValueError("unverified_billing_content")
    if (payload.tenant, payload.entity, payload.canonical_account) != (
        effect.tenant,
        effect.entity,
        effect.canonical_account,
    ):
        raise ValueError("cross_entity_billing")
    invoices = [x for x in receipt["records"] if x["kind"] == "invoice"]
    if len(invoices) != 1:
        raise ValueError("one_invoice_required")
    invoice = invoices[0]
    if type(invoice["amount_cents"]) is not int or invoice["amount_cents"] <= 0:
        raise ValueError("invalid_invoice_amount")
    posting = base_record(
        effect,
        "posting",
        "posting",
        invoice_id=invoice["id"],
        posting_date=invoice["invoice_date"],
        amount_cents=invoice["amount_cents"],
        debit_account="accounts_receivable",
        credit_account="deferred_revenue",
        posting_type="invoice_not_revenue_recognition",
    )
    records = [posting]
    allocations = [x for x in receipt["records"] if x["kind"] == "payment_allocation"]
    if any(
        type(x["amount_cents"]) is not int
        or x["amount_cents"] <= 0
        or x["invoice_id"] != invoice["id"]
        for x in allocations
    ):
        raise ValueError("invalid_payment_allocation")
    if sum(x["amount_cents"] for x in allocations) > invoice["amount_cents"]:
        raise ValueError("overapplication")
    for allocation in allocations:
        records.append(
            base_record(
                effect,
                "payment_application",
                allocation["id"],
                invoice_id=invoice["id"],
                allocation_id=allocation["id"],
                amount_cents=allocation["amount_cents"],
                posting_date=allocation["payment_date"],
            )
        )
    return records
