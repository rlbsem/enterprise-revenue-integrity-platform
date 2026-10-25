"""Explicit commercial changes, immutable billing amendments and independent ERP effects."""

from datetime import datetime

from fastapi import Depends, HTTPException
from psycopg.types.json import Jsonb

from .contracts import ContractChange, digest
from .db import connect, lock


def latest_changes(changes, known_at=None):
    heads = {}
    for change in changes:
        if known_at and datetime.fromisoformat(
            change["observed_source_at"]
        ) > datetime.fromisoformat(known_at):
            continue
        old = heads.get(change["change_id"])
        if old is None or old["revision"] < change["revision"]:
            heads[change["change_id"]] = change
    return sorted(heads.values(), key=lambda x: (x["effective_start"], x["revision"]))


def monthly_at(base, changes, day):
    amount = base["unit_monthly_cents"] * base["quantity"]
    for change in latest_changes(changes):
        if change["effective_start"] <= day:
            amount = change["unit_monthly_cents"] * change["quantity"]
    return amount


def quarter_charge(base, changes, invoice_date):
    year, month = map(int, invoice_date[:7].split("-"))
    total = 0
    for offset in range(3):
        index = year * 12 + month - 1 + offset
        day = f"{index // 12:04}-{index % 12 + 1:02}-01"
        total += monthly_at(base, changes, day)
    return total


def mount_source(app, dsn, injector):
    @app.post("/test/contract-changes", dependencies=[Depends(injector)])
    def create_change(command: ContractChange):
        body = command.model_dump(mode="json")
        with connect(dsn) as c:
            lock(c, command.tenant, command.opportunity_id)
            prior = c.execute("SELECT body FROM events WHERE id=%s", (command.event_id,)).fetchone()
            if prior:
                if prior["body"] != body:
                    raise HTTPException(409, "event_identity_content_conflict")
                return {"duplicate": True, "event_id": command.event_id, "hash": digest(body)}
            current = c.execute(
                "SELECT body FROM opportunities WHERE id=%s AND tenant=%s AND entity=%s FOR UPDATE",
                (command.opportunity_id, command.tenant, command.entity),
            ).fetchone()
            original = c.execute(
                "SELECT body FROM contracts WHERE id=%s AND tenant=%s AND entity=%s",
                (command.contract_id, command.tenant, command.entity),
            ).fetchone()
            if not current or not original:
                raise HTTPException(409, "commercial_scope_missing")
            old = current["body"]
            base = original["body"]
            if (command.parent_event_id, command.revision) != (
                old["event_id"],
                old["revision"] + 1,
            ):
                raise HTTPException(409, "stale_commercial_compare_and_swap")
            if any(
                body[k] != old[k]
                for k in (
                    "tenant",
                    "entity",
                    "account_id",
                    "contract_id",
                    "opportunity_id",
                    "owner_id",
                    "currency",
                )
            ):
                raise HTTPException(409, "commercial_identity_or_owner_changed")
            history = [
                r["body"]
                for r in c.execute(
                    "SELECT body FROM events WHERE body->>'contract_id'=%s AND tenant=%s AND entity=%s",
                    (command.contract_id, command.tenant, command.entity),
                )
                if r["body"].get("schema_version") == "contract-change.v1"
            ]
            if datetime.fromisoformat(command.observed_source_at) < datetime.fromisoformat(
                old.get("observed_source_at", old["event_at"])
            ):
                raise HTTPException(409, "observation_time_regression")
            logical = [r for r in history if r["change_id"] == command.change_id]
            if command.action == "correction":
                if (
                    not logical
                    or max(logical, key=lambda x: x["revision"])["event_id"]
                    != command.correction_of
                ):
                    raise HTTPException(409, "invalid_correction_target")
                if (
                    command.effective_start
                    != max(logical, key=lambda x: x["revision"])["effective_start"]
                    or command.effective_end
                    != max(logical, key=lambda x: x["revision"])["effective_end"]
                ):
                    raise HTTPException(409, "correction_interval_change_requires_new_amendment")
            elif logical:
                raise HTTPException(409, "change_identity_already_used")
            amount = command.unit_monthly_cents * command.quantity
            previous = monthly_at(base, history, command.effective_start)
            if (
                command.action == "expansion"
                and amount <= previous
                or command.action == "contraction"
                and not 0 < amount < previous
            ):
                raise HTTPException(409, "change_direction_invalid")
            if command.action == "renewal":
                if command.effective_start != max(
                    [base["effective_end"], *[r["effective_end"] for r in history]]
                ):
                    raise HTTPException(409, "renewal_must_follow_term")
            elif not base["effective_start"] <= command.effective_start < base["effective_end"]:
                raise HTTPException(409, "amendment_outside_original_term")
            c.execute(
                "INSERT INTO events VALUES(%s,%s,%s,%s)",
                (command.tenant, command.entity, command.event_id, Jsonb(body)),
            )
            c.execute(
                "UPDATE opportunities SET body=%s WHERE id=%s",
                (Jsonb(body), command.opportunity_id),
            )
        return {"duplicate": False, "event_id": command.event_id, "hash": digest(body)}


def verify_source(controller, event):
    from .control import Refused

    ContractChange.model_validate(event)
    scope = {"tenant": event["tenant"], "entity": event["entity"]}
    with controller.client("salesforce") as client:
        responses = [
            client.get(route + event[field], params=scope)
            for route, field in (
                ("/events/", "event_id"),
                ("/opportunities/", "opportunity_id"),
                ("/accounts/", "account_id"),
                ("/contracts/", "contract_id"),
                ("/events/", "parent_event_id"),
            )
        ]
    if any(r.status_code != 200 for r in responses):
        raise Refused("commercial_change_source_coverage_gap")
    saved, current, account, contract, parent = [r.json() for r in responses]
    if saved != event or current != event or account.get("Id") != event["account_id"]:
        raise Refused("commercial_change_not_current_or_forged")
    if any(
        contract[k] != event[k]
        for k in ("tenant", "entity", "account_id", "contract_id", "currency")
    ):
        raise Refused("commercial_change_identity_mismatch")
    if (parent["revision"] + 1, parent["event_id"]) != (
        event["revision"],
        event["parent_event_id"],
    ):
        raise Refused("commercial_change_history_gap")


def ingest(controller, document):
    from .control import Refused

    event = ContractChange.model_validate(document).model_dump(mode="json")
    with connect(controller.dsn) as c:
        lock(c, event["tenant"], event["opportunity_id"])
        old = c.execute(
            "SELECT * FROM inbox WHERE tenant=%s AND event_id=%s",
            (event["tenant"], event["event_id"]),
        ).fetchone()
        if old:
            if old["body"] != event:
                raise Refused("event_identity_content_conflict")
            return {"status": old["status"], "duplicate": True}
        c.execute(
            "INSERT INTO inbox(tenant,event_id,entity,opportunity_id,revision,hash,body,status) VALUES(%s,%s,%s,%s,%s,%s,%s,'registered')",
            (
                event["tenant"],
                event["event_id"],
                event["entity"],
                event["opportunity_id"],
                event["revision"],
                digest(event),
                Jsonb(event),
            ),
        )
        try:
            controller.source(event)
            head = c.execute(
                "SELECT * FROM source_heads WHERE tenant=%s AND opportunity_id=%s",
                (event["tenant"], event["opportunity_id"]),
            ).fetchone()
            if not head or (head["event_id"], head["revision"] + 1) != (
                event["parent_event_id"],
                event["revision"],
            ):
                raise Refused("unverified_parent_or_revision_gap")
            parent = c.execute(
                "SELECT status FROM inbox WHERE tenant=%s AND event_id=%s",
                (event["tenant"], event["parent_event_id"]),
            ).fetchone()
            if parent["status"] != "completed":
                raise Refused("parent_effect_not_completed")
            account, maps = controller.mapping(c, event)
            controller.intent(c, event, account, maps["stripe"], "stripe", event)
            c.execute(
                "UPDATE source_heads SET revision=%s,event_id=%s WHERE tenant=%s AND opportunity_id=%s",
                (event["revision"], event["event_id"], event["tenant"], event["opportunity_id"]),
            )
            state, reason = "planned", ""
        except Refused as exc:
            state, reason = "blocked", str(exc)
            controller.exception(c, event, reason)
        c.execute(
            "UPDATE inbox SET status=%s,detail=%s WHERE tenant=%s AND event_id=%s",
            (state, reason, event["tenant"], event["event_id"]),
        )
        controller.audit(c, event, state, event["event_id"], {"reason": reason})
    return {"status": state, "detail": reason, "duplicate": False}


def receive_billing(c, effect):
    from .services import base_record

    event = ContractChange.model_validate(effect.facts).model_dump(mode="json")
    if (
        any(event[k] != getattr(effect, k) for k in ("tenant", "entity", "currency"))
        or event["event_id"] != effect.source_event
    ):
        raise ValueError("change_scope_mismatch")
    subscriptions = c.execute(
        "SELECT body FROM records WHERE kind='subscription' AND tenant=%s AND entity=%s AND body->>'contract_id'=%s ORDER BY id",
        (effect.tenant, effect.entity, event["contract_id"]),
    ).fetchall()
    if not subscriptions:
        raise ValueError("original_subscription_missing")
    original = min((r["body"] for r in subscriptions), key=lambda x: x["effective_start"])
    item = c.execute(
        "SELECT body FROM records WHERE kind='subscription_item' AND body->>'subscription_id'=%s",
        (original["id"],),
    ).fetchone()["body"]
    changes = [
        r["body"]["change"]
        for r in c.execute(
            "SELECT body FROM records WHERE kind='subscription_change' AND tenant=%s AND entity=%s AND body->>'contract_id'=%s",
            (effect.tenant, effect.entity, event["contract_id"]),
        )
    ]
    if any(x["event_id"] == event["event_id"] for x in changes):
        raise ValueError("change_already_applied_with_other_effect")
    if event["parent_event_id"] != (
        max(changes, key=lambda x: x["revision"])["event_id"]
        if changes
        else original["source_event"]
    ):
        raise ValueError("receiver_change_history_gap")
    change = base_record(
        effect,
        "subscription_change",
        "change",
        contract_id=event["contract_id"],
        subscription_id=original["id"],
        change=event,
    )
    records = [change]
    if event["action"] == "renewal":
        sub = base_record(
            effect,
            "subscription",
            "renewal",
            contract_id=event["contract_id"],
            effective_start=event["effective_start"],
            effective_end=event["effective_end"],
            billing_interval_months=3,
            status="active",
            observed_source_at=event["observed_source_at"],
        )
        monthly = event["unit_monthly_cents"] * event["quantity"]
        new_item = base_record(
            effect,
            "subscription_item",
            "renewal-item",
            subscription_id=sub["id"],
            unit_monthly_cents=event["unit_monthly_cents"],
            quantity=event["quantity"],
            monthly_cents=monthly,
        )
        invoice = base_record(
            effect,
            "invoice",
            "renewal-invoice",
            subscription_id=sub["id"],
            invoice_date=event["effective_start"],
            amount_cents=monthly * 3,
            status="open",
        )
        line = base_record(
            effect,
            "invoice_line",
            "renewal-line",
            invoice_id=invoice["id"],
            item_id=new_item["id"],
            amount_cents=monthly * 3,
            service_months=3,
        )
        records.extend([sub, new_item, invoice, line])
    else:
        invoices = c.execute(
            "SELECT body FROM records WHERE kind='invoice' AND body->>'subscription_id'=%s",
            (original["id"],),
        ).fetchall()
        for row in invoices:
            invoice = row["body"]
            delta = quarter_charge(
                item, changes + [event], invoice["invoice_date"]
            ) - quarter_charge(item, changes, invoice["invoice_date"])
            if delta:
                records.append(
                    base_record(
                        effect,
                        "amendment_line",
                        invoice["id"],
                        invoice_id=invoice["id"],
                        contract_id=event["contract_id"],
                        amount_cents=delta,
                        effective_date=event["effective_start"],
                        observed_source_at=event["observed_source_at"],
                    )
                )
    return records


def receive_finance(effect):
    from .services import base_record, finance_records

    receipt = effect.facts["billing_receipt"]
    if receipt["payload_hash"] != digest(receipt["payload"]) or receipt["records_hash"] != digest(
        receipt["records"]
    ):
        raise ValueError("changed_billing_receipt")
    if (
        receipt["payload"]["tenant"] != effect.tenant
        or receipt["payload"]["entity"] != effect.entity
    ):
        raise ValueError("cross_scope_change")
    result = []
    if any(x["kind"] == "invoice" for x in receipt["records"]):
        compatible = effect.model_copy(update={"operation": "post_invoice_and_cash"})
        result.extend(finance_records(compatible))
    for line in receipt["records"]:
        if line["kind"] == "amendment_line":
            result.append(
                base_record(
                    effect,
                    "change_posting",
                    line["id"],
                    invoice_id=line["invoice_id"],
                    amendment_line_id=line["id"],
                    amount_cents=line["amount_cents"],
                    posting_date=line["effective_date"],
                    observed_source_at=line["observed_source_at"],
                )
            )
    if not result:
        result.append(
            base_record(
                effect,
                "change_confirmation",
                "confirmation",
                change_event=effect.source_event,
                amount_cents=0,
            )
        )
    return result
