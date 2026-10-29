import time

from psycopg.types.json import Jsonb

from integrity.control import Controller
from integrity.db import connect


def rows(stack, database, table):
    # Table names are constants in trusted acceptance code, never user input.
    with connect(stack.admins[database]) as c:
        return c.execute("SELECT * FROM " + table).fetchall()


def wait_for(probe, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = probe()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("acceptance_boundary_not_observed")


def create_other_tenant(stack):
    event = {
        **stack.events[0],
        "tenant": "other",
        "event_id": "other-event",
        "opportunity_id": "other-opportunity",
        "contract_id": "other-contract",
        "account_id": "other-account",
    }
    with connect(stack.admins["salesforce"]) as c:
        for table, identity, body in [
            ("events", event["event_id"], event),
            ("opportunities", event["opportunity_id"], event),
            (
                "accounts",
                event["account_id"],
                {
                    "Id": event["account_id"],
                    "OwnerId": event["owner_id"],
                    "Name": "Synthetic other tenant",
                },
            ),
            (
                "contracts",
                event["contract_id"],
                {
                    k: event[k]
                    for k in [
                        "account_id",
                        "contract_id",
                        "tenant",
                        "entity",
                        "currency",
                        "unit_monthly_cents",
                        "quantity",
                        "effective_start",
                        "effective_end",
                    ]
                },
            ),
        ]:
            c.execute(
                "INSERT INTO " + table + " VALUES(%s,%s,%s,%s)",
                ("other", "US", identity, Jsonb(body)),
            )
    with connect(stack.admins["control"]) as c:
        c.execute(
            "INSERT INTO accounts VALUES('other','US','other-canonical','Synthetic other tenant')"
        )
        for source, identity in [
            ("salesforce", event["account_id"]),
            ("stripe", "other-billing"),
            ("netsuite", "other-finance"),
        ]:
            c.execute(
                "INSERT INTO mappings VALUES('other','US','other-canonical',%s,%s,1,'approved','isolated-adversarial-fixture')",
                (source, identity),
            )
    for destination, identity in [("stripe", "other-billing"), ("netsuite", "other-finance")]:
        with connect(stack.admins[destination]) as c:
            c.execute(
                "INSERT INTO customers VALUES('other','US',%s,'other-canonical')", (identity,)
            )
    stack.config["approved_scopes"].append(["other", "US"])
    controller = Controller(stack.config)
    for source_event in [*stack.events[:2], event]:
        assert controller.ingest(source_event)["status"] == "planned"
    controller.drain()
    return event


def exhaust(stack, controller):
    import httpx

    controller.ingest(stack.events[0])
    intent = rows(stack, "control", "outbox")[0]
    lane = stack.config["services"]["stripe"]
    r = httpx.post(
        lane["url"] + "/test/faults",
        headers={"Authorization": "Bearer " + stack.injector},
        json={"effect_id": str(intent["effect_id"]), "modes": ["unavailable"] * 3},
        trust_env=False,
    )
    assert r.status_code == 200
    for _ in range(3):
        assert controller.step(intent["effect_id"]) == "uncertain"
    assert controller.step(intent["effect_id"]) == "exhausted"
    return intent
