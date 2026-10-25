"""Deterministic business facts; runtime identities/credentials are never fixture facts."""

from psycopg.types.json import Jsonb

from .contracts import Commercial
from .db import connect


def scenario():
    result = []
    for index, entity, unit, quantity in [
        (1, "US", 125000, 8),
        (2, "EU", 80000, 5),
        (3, "US", 45000, 4),
    ]:
        result.append(
            Commercial(
                tenant="northstar",
                entity=entity,
                event_id=f"sf-event-{index}-v1",
                opportunity_id=f"006-synthetic-{index}",
                account_id=f"001-synthetic-{index}",
                contract_id=f"800-synthetic-{index}",
                owner_id=f"005-owner-{entity}",
                revision=1,
                stage="Closed Won",
                unit_monthly_cents=unit,
                quantity=quantity,
                effective_start="2026-01-01",
                effective_end="2027-01-01",
                event_at="2025-12-20T14:00:00+00:00",
            ).model_dump(mode="json")
        )
    return result


def seed(config, admins):
    events = scenario()
    with connect(admins["salesforce"]) as c:
        for event in events:
            scope = (event["tenant"], event["entity"])
            c.execute(
                "INSERT INTO accounts VALUES(%s,%s,%s,%s)",
                (
                    *scope,
                    event["account_id"],
                    Jsonb(
                        {
                            "Id": event["account_id"],
                            "OwnerId": event["owner_id"],
                            "Name": "Synthetic " + event["entity"] + " " + event["account_id"],
                        }
                    ),
                ),
            )
            commitment = {
                k: event[k]
                for k in (
                    "account_id",
                    "contract_id",
                    "tenant",
                    "entity",
                    "currency",
                    "unit_monthly_cents",
                    "quantity",
                    "effective_start",
                    "effective_end",
                )
            }
            c.execute(
                "INSERT INTO contracts VALUES(%s,%s,%s,%s)",
                (*scope, event["contract_id"], Jsonb(commitment)),
            )
            c.execute(
                "INSERT INTO events VALUES(%s,%s,%s,%s)", (*scope, event["event_id"], Jsonb(event))
            )
            c.execute(
                "INSERT INTO opportunities VALUES(%s,%s,%s,%s)",
                (*scope, event["opportunity_id"], Jsonb(event)),
            )
    with connect(admins["control"]) as c:
        for index, event in enumerate(events, 1):
            scope = (event["tenant"], event["entity"], "canonical-" + str(index))
            c.execute(
                "INSERT INTO accounts VALUES(%s,%s,%s,%s)",
                (*scope, "Synthetic account " + str(index)),
            )
            for source, source_id in [
                ("salesforce", event["account_id"]),
                ("stripe", f"cus-synthetic-{index}"),
                ("netsuite", f"ns-synthetic-{index}"),
            ]:
                c.execute(
                    "INSERT INTO mappings VALUES(%s,%s,%s,%s,%s,1,'approved','synthetic-reviewed-map.v1')",
                    (*scope, source, source_id),
                )
        # A second plausible canonical account, not an auto-selected first match.
        c.execute(
            "INSERT INTO accounts VALUES('northstar','US','ambiguous-other','Synthetic mapping collision')"
        )
        c.execute(
            "INSERT INTO mappings VALUES('northstar','US','ambiguous-other','salesforce',%s,1,'approved',"
            "'synthetic-ambiguity-negative-case')",
            (events[2]["account_id"],),
        )
    for destination in ("stripe", "netsuite"):
        with connect(admins[destination]) as c:
            for index, event in enumerate(events, 1):
                customer = ("cus" if destination == "stripe" else "ns") + f"-synthetic-{index}"
                c.execute(
                    "INSERT INTO customers VALUES(%s,%s,%s,%s)",
                    (event["tenant"], event["entity"], customer, f"canonical-{index}"),
                )
                if destination == "stripe":
                    # Independently received cash, not an inferred Closed Won payment.
                    payment = {
                        "kind": "payment",
                        "id": f"payment-{index}",
                        "tenant": event["tenant"],
                        "entity": event["entity"],
                        "customer": customer,
                        "currency": "USD",
                        "amount_cents": 1200000 if index == 1 else 450000,
                        "payment_date": "2026-01-10",
                        "source": "synthetic-bank-settlement.v1",
                    }
                    c.execute(
                        "INSERT INTO records VALUES('payment',%s,%s,%s,%s)",
                        (payment["id"], event["tenant"], event["entity"], Jsonb(payment)),
                    )
    return events
