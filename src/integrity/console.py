"""Launch the real local operator application with isolated synthetic services and credentials."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from psycopg.types.json import Jsonb

from .control import Controller
from .db import connect
from .harness import LocalStack
from .lifecycle_demo import execute_lifecycle


def seed_recovery(stack):
    event = {
        **stack.events[0],
        "event_id": "sf-recovery-v1",
        "account_id": "001-synthetic-recovery",
        "contract_id": "800-synthetic-recovery",
        "opportunity_id": "006-synthetic-recovery",
        "unit_monthly_cents": 110000,
        "quantity": 2,
    }
    with connect(stack.admins["salesforce"]) as c:
        c.execute(
            "INSERT INTO accounts VALUES(%s,%s,%s,%s)",
            (
                event["tenant"],
                event["entity"],
                event["account_id"],
                Jsonb(
                    {
                        "Id": event["account_id"],
                        "OwnerId": event["owner_id"],
                        "Name": "Synthetic recovery account",
                    }
                ),
            ),
        )
        fields = (
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
        c.execute(
            "INSERT INTO contracts VALUES(%s,%s,%s,%s)",
            (
                event["tenant"],
                event["entity"],
                event["contract_id"],
                Jsonb({k: event[k] for k in fields}),
            ),
        )
        c.execute(
            "INSERT INTO events VALUES(%s,%s,%s,%s)",
            (event["tenant"], event["entity"], event["event_id"], Jsonb(event)),
        )
        c.execute(
            "INSERT INTO opportunities VALUES(%s,%s,%s,%s)",
            (event["tenant"], event["entity"], event["opportunity_id"], Jsonb(event)),
        )
    with connect(stack.admins["control"]) as c:
        c.execute(
            "INSERT INTO accounts VALUES('northstar','US','canonical-recovery','Synthetic recovery account')"
        )
        for source, identity in (
            ("salesforce", event["account_id"]),
            ("stripe", "cus-synthetic-recovery"),
            ("netsuite", "ns-synthetic-recovery"),
        ):
            c.execute(
                "INSERT INTO mappings VALUES('northstar','US','canonical-recovery',%s,%s,1,'approved','synthetic-recovery-map.v1')",
                (source, identity),
            )
    for source, prefix in (("stripe", "cus"), ("netsuite", "ns")):
        with connect(stack.admins[source]) as c:
            c.execute(
                "INSERT INTO customers VALUES('northstar','US',%s,'canonical-recovery')",
                (prefix + "-synthetic-recovery",),
            )
    return event


def prepare(stack):
    event = seed_recovery(stack)
    result = execute_lifecycle(stack, stack.workspace / "acceptance")
    controller = Controller(stack.config)
    assert controller.ingest(event)["status"] == "planned"
    with connect(stack.config["control_dsn"]) as c:
        intent = c.execute(
            "SELECT effect_id FROM outbox WHERE event_id=%s", (event["event_id"],)
        ).fetchone()["effect_id"]
    lane = stack.config["services"]["stripe"]
    response = httpx.post(
        lane["url"] + "/test/faults",
        json={"effect_id": str(intent), "modes": ["unavailable"] * 3},
        headers={"Authorization": "Bearer " + stack.injector},
        trust_env=False,
    )
    response.raise_for_status()
    for _ in range(3):
        assert controller.step(intent) == "uncertain"
    assert controller.step(intent) == "exhausted"
    return result, str(intent)


def start_api(stack, port=8582):
    stack.config["console_origin"] = f"http://127.0.0.1:{port}"
    stack.config["console_workspace"] = str(stack.workspace / "console-worker")
    path = stack.workspace / "console-private.json"
    path.write_bytes(json.dumps(stack.config).encode())
    if os.name != "nt":
        path.chmod(0o600)
    log = (stack.workspace / "console.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "integrity.console_factory:factory",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        env={**os.environ, "INTEGRITY_CONSOLE_CONFIG": str(path)},
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    stack.processes.append((process, log))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("console_start_failed_see_private_log")
        try:
            if httpx.get(stack.config["console_origin"] + "/", trust_env=False).status_code == 200:
                return process
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("console_not_ready")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--port", type=int, default=8582)
    parser.add_argument("--stop-file", help="Private test-runner shutdown marker")
    a = parser.parse_args()
    with LocalStack(a.workspace) as stack:
        prepare(stack)
        process = start_api(stack, a.port)
        credentials = stack.workspace / "operator-credentials.json"
        credentials.write_bytes(
            json.dumps(
                {
                    u: {
                        "username": u,
                        "password": v["password"],
                        "role": v["role"],
                        "scopes": v["scopes"],
                    }
                    for u, v in stack.operators.items()
                },
                indent=2,
            ).encode()
        )
        if os.name != "nt":
            credentials.chmod(0o600)
        print(
            json.dumps(
                {
                    "url": stack.config["console_origin"],
                    "private_credentials_file": str(credentials),
                    "lane": "local_synthetic_contracts",
                }
            ),
            flush=True,
        )
        try:
            while process.poll() is None:
                if a.stop_file and Path(a.stop_file).exists():
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
