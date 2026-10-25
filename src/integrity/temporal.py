"""Independent source-history reconstruction. Does not use receiver pricing helpers."""

from datetime import date, datetime, timedelta

from .contracts import ContractChange


def months(period):
    start = date.fromisoformat(period["start"])
    end = date.fromisoformat(period["end_exclusive"])
    if start.day != 1 or end.day != 1 or end <= start:
        raise ValueError("whole_month_reporting_period_required")
    values = []
    current = start
    while current < end:
        index = current.year * 12 + current.month
        following = date(index // 12, index % 12 + 1, 1)
        values.append((current.isoformat(), following.isoformat()))
        current = following
    if len(values) != 3:
        raise ValueError("quarterly_reporting_period_required")
    if datetime.fromisoformat(period["known_at"]).utcoffset() is None:
        raise ValueError("aware_knowledge_cutoff_required")
    return values


def changes(snapshot):
    result = []
    for source in snapshot["snapshots"]:
        if source["service"] == "salesforce":
            for event in source["contents"]["events"]:
                if event.get("schema_version") == "contract-change.v1":
                    ContractChange.model_validate(event)
                    if (event["tenant"], event["entity"]) != (source["tenant"], source["entity"]):
                        raise ValueError("commercial_change_scope_mismatch")
                    result.append(event)
    return result


def known_heads(events, cutoff):
    selected = {}
    for event in events:
        if datetime.fromisoformat(event["observed_source_at"]) <= datetime.fromisoformat(cutoff):
            identity = (event["tenant"], event["entity"], event["contract_id"], event["change_id"])
            prior = selected.get(identity)
            if prior is None or prior["revision"] < event["revision"]:
                selected[identity] = event
    return list(selected.values())


def charge(snapshot, contract, invoice_date):
    if invoice_date != snapshot["period"]["start"]:
        raise ValueError("quarter_aligned_invoice_required")
    history = known_heads(changes(snapshot), snapshot["period"]["known_at"])
    total = 0
    for day, _ in months(snapshot["period"]):
        amount = (
            contract["unit_monthly_cents"] * contract["quantity"]
            if contract["effective_start"] <= day < contract["effective_end"]
            else 0
        )
        candidates = [
            r
            for r in history
            if (r["tenant"], r["entity"], r["contract_id"])
            == (contract["tenant"], contract["entity"], contract["contract_id"])
            and r["effective_start"] <= day < r["effective_end"]
        ]
        if candidates:
            winner = max(candidates, key=lambda r: (r["effective_start"], r["revision"]))
            amount = winner["unit_monthly_cents"] * winner["quantity"]
        total += amount
    return total


def billing_arr(snapshot, records):
    at = (date.fromisoformat(snapshot["period"]["end_exclusive"]) - timedelta(days=1)).isoformat()
    known = snapshot["period"]["known_at"]
    subscriptions = {
        r["id"]: r for r in records if r["source"] == "stripe" and r["kind"] == "subscription"
    }
    events = known_heads(
        [
            r["change"]
            for r in records
            if r["source"] == "stripe" and r["kind"] == "subscription_change"
        ],
        known,
    )
    result = {}
    for item in (
        r for r in records if r["source"] == "stripe" and r["kind"] == "subscription_item"
    ):
        sub = subscriptions[item["subscription_id"]]
        if not sub["effective_start"] <= at < sub["effective_end"]:
            continue
        amount = item["unit_monthly_cents"] * item["quantity"]
        eligible = [
            e
            for e in events
            if (e["tenant"], e["entity"], e["contract_id"])
            == (sub["tenant"], sub["entity"], sub["contract_id"])
            and e["effective_start"] <= at < e["effective_end"]
        ]
        if eligible:
            e = max(eligible, key=lambda x: (x["effective_start"], x["revision"]))
            amount = e["unit_monthly_cents"] * e["quantity"]
        scope = (item["tenant"], item["entity"])
        result[scope] = result.get(scope, 0) + amount * 12
    return result
