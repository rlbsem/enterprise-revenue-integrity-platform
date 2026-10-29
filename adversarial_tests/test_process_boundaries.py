import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from integrity.control import Controller
from integrity.db import connect

from .helpers import rows, wait_for


@pytest.mark.parametrize(
    "phase", ["intent", "inflight_dispatch", "receipt_before_confirmation", "finance_pending"]
)
def test_actual_worker_termination_at_observed_transaction_boundaries(
    stack, tmp_path, phase, observe
):
    controller = Controller(stack.config)
    marker = tmp_path / "boundary"
    event = tmp_path / "event.json"
    event.write_text(json.dumps(stack.events[0]))
    held = None
    if phase == "intent":
        mode, argument = "ingest", str(event)
    else:
        controller.ingest(stack.events[0])
        effect = rows(stack, "control", "outbox")[0]
        mode, argument = "step", str(effect["effect_id"])
        if phase == "inflight_dispatch":
            held = psycopg.connect(stack.admins["stripe"], autocommit=True)
            held.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (argument,))
        elif phase == "receipt_before_confirmation":
            held = psycopg.connect(stack.admins["control"], autocommit=True)
            held.execute("SELECT pg_advisory_lock(814920713)")
            held.execute(
                "CREATE FUNCTION acceptance_pause_confirmation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(814920713); RETURN NEW; END $$"
            )
            held.execute(
                "CREATE TRIGGER acceptance_confirmation BEFORE UPDATE OF status ON outbox FOR EACH ROW WHEN(NEW.status='verified' AND OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION acceptance_pause_confirmation()"
            )
    child = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("worker_probe.py")),
            str(stack.config_path),
            mode,
            argument,
            str(marker),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if phase in ["intent", "finance_pending"]:
            wait_for(lambda: marker.exists(), 20)
        else:
            database = conninfo_to_dict(
                stack.admins["stripe" if phase == "inflight_dispatch" else "control"]
            )["dbname"]

            def waiting():
                with connect(stack.admin) as c:
                    return (
                        c.execute(
                            "SELECT count(*) n FROM pg_stat_activity WHERE datname=%s AND wait_event='advisory'",
                            (database,),
                        ).fetchone()["n"]
                        > 0
                    )

            wait_for(waiting, 20)
        before = {d: len(rows(stack, d, "effects")) for d in ["stripe", "netsuite"]}
        if phase in ["intent", "inflight_dispatch"]:
            assert before == {"stripe": 0, "netsuite": 0}
        else:
            assert before == {"stripe": 1, "netsuite": 0}
        if phase == "receipt_before_confirmation":
            assert rows(stack, "control", "outbox")[0]["proof"] is None
            assert any(x["operation"] == "readback" for x in rows(stack, "stripe", "requests"))
        child.kill()
        child.wait(timeout=10)
        assert child.returncode != 0
        if held:
            held.execute("SELECT pg_advisory_unlock_all()")
            if phase == "receipt_before_confirmation":
                held.execute("DROP TRIGGER acceptance_confirmation ON outbox")
                held.execute("DROP FUNCTION acceptance_pause_confirmation()")
            held.close()
            held = None
        if phase == "inflight_dispatch":
            wait_for(lambda: len(rows(stack, "stripe", "effects")) == 1)
        controller.drain(due_only=False)
        assert len(rows(stack, "stripe", "effects")) == len(rows(stack, "netsuite", "effects")) == 1
        assert all(x["status"] == "verified" for x in rows(stack, "control", "outbox"))
        successful_posts = {
            d: len([x for x in rows(stack, d, "requests") if x["operation"] == "apply"])
            for d in ["stripe", "netsuite"]
        }
        assert successful_posts == {"stripe": 1, "netsuite": 1}
        observe(
            phase=phase,
            actual_child_terminated=True,
            exit_code=child.returncode,
            receiver_effects_before_termination=before,
            receiver_effects_after_restart={"stripe": 1, "netsuite": 1},
            independent_successful_applications=successful_posts,
            gate=controller.verify_recovery(),
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        if held:
            held.execute("SELECT pg_advisory_unlock_all()")
            if phase == "receipt_before_confirmation":
                held.execute("DROP TRIGGER IF EXISTS acceptance_confirmation ON outbox")
                held.execute("DROP FUNCTION IF EXISTS acceptance_pause_confirmation()")
            held.close()


def test_real_receiver_restart_retains_independent_effects(stack, observe):
    controller = Controller(stack.config)
    controller.ingest(stack.events[0])
    controller.drain()
    index = 1  # fixture service order is Salesforce, billing, finance
    process, log = stack.processes[index]
    process.terminate()
    process.wait(timeout=10)
    assert not controller.verify_recovery()["safe_to_resume"]
    lane = stack.config["services"]["stripe"]
    port = int(lane["url"].rsplit(":", 1)[1])
    restarted = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "integrity.services:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        env={
            **os.environ,
            "SERVICE_KIND": "stripe",
            "SERVICE_DSN": stack.app_dsns["stripe"],
            "SERVICE_TOKEN": lane["token"],
            "INJECTOR_TOKEN": stack.injector,
        },
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    stack.processes[index] = (restarted, log)

    def available():
        try:
            return httpx.get(lane["url"] + "/health", trust_env=False, timeout=1).status_code == 200
        except httpx.HTTPError:
            return False

    wait_for(available)
    assert controller.verify_recovery()["safe_to_resume"]
    controller.drain()
    assert len(rows(stack, "stripe", "effects")) == len(rows(stack, "netsuite", "effects")) == 1
    observe(
        actual_receiver_process_restart=True,
        retained_effects={"stripe": 1, "netsuite": 1},
        gate=controller.verify_recovery(),
    )


def test_owned_postgresql_restart_preserves_roles_receipts_and_financial_values(stack, observe):
    assert stack.own_cluster, "acceptance_requires_owned_disposable_PostgreSQL"
    controller = Controller(stack.config)
    for event in stack.events[:2]:
        controller.ingest(event)
    controller.drain()
    before = {d: rows(stack, d, "effects") for d in ["stripe", "netsuite"]}
    options = conninfo_to_dict(stack.admin)
    subprocess.run(
        [stack.pg_ctl, "-D", str(stack.workspace / "pgdata"), "-m", "fast", "-w", "stop"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    with pytest.raises(psycopg.OperationalError):
        controller.verify_recovery()
    subprocess.run(
        [
            stack.pg_ctl,
            "-D",
            str(stack.workspace / "pgdata"),
            "-l",
            str(stack.workspace / "postgres.log"),
            "-o",
            f"-h 127.0.0.1 -p {options['port']} -c max_connections=40 -c shared_buffers=64MB",
            "-w",
            "start",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    assert controller.verify_recovery()["safe_to_resume"]
    assert before == {d: rows(stack, d, "effects") for d in ["stripe", "netsuite"]}
    with connect(stack.operators["reader_eu"]["dsn"]) as c:
        assert {r["entity"] for r in c.execute("SELECT entity FROM outbox")} == {"EU"}
    observe(
        actual_owned_PostgreSQL_restart=True,
        independent_receipts_unchanged=True,
        restricted_scope_retained=True,
        gate=controller.verify_recovery(),
    )
