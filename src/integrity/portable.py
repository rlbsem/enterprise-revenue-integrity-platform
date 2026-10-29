"""Disposable portable wrapper over the existing four-database operator application."""

import argparse
import hashlib
import json
import os
import secrets
import signal
import socket
import threading
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from psycopg.conninfo import conninfo_to_dict
from .console import prepare, start_api
from .harness import LocalStack
from .db import connect


def private(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes((json.dumps(value, indent=2, default=str) + "\n").encode())
    if os.name != "nt":
        temporary.chmod(0o600)
    temporary.replace(path)


@contextmanager
def lock(state):
    if state.is_symlink():
        raise ValueError("owned_state_required")
    state.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        state.chmod(0o700)
    with (state / "owner.lock").open("a+b") as f:
        f.write(b"0")
        f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("demo_state_already_in_use") from error
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def current(state):
    value = json.loads((state / "ready-private.json").read_bytes())
    run = (state / value["run"]).resolve()
    if not run.is_relative_to(state.resolve()) or not run.is_dir():
        raise ValueError("invalid_owned_run")
    return value, run


def serve(state, port=8582, container=False, public_port=None):
    if any(
        os.environ.get(k)
        for k in [
            "INTEGRITY_ADMIN_DSN",
            "INTEGRITY_CONSOLE_CONFIG",
            "SERVICE_DSN",
            "SERVICE_TOKEN",
            "INJECTOR_TOKEN",
        ]
    ):
        raise ValueError("portable_refuses_external_runtime")
    if container and not Path("/.dockerenv").exists():
        raise ValueError("container_binding_requires_docker")
    if not 0 < port < 65536 or not 0 < (public_port or port) < 65536:
        raise ValueError("valid_port_required")
    # Fail before preparing expensive synthetic fixtures; the server still owns
    # the final bind and fails closed if another process wins a later race.
    with socket.socket() as probe:
        probe.bind(("0.0.0.0" if container else "127.0.0.1", port))
    stop = threading.Event()
    prior = {s: signal.signal(s, lambda *_: stop.set()) for s in [signal.SIGINT, signal.SIGTERM]}
    pointer = state / "ready-private.json"
    try:
        with lock(state):
            pointer.unlink(missing_ok=True)
            (state / "shutdown.json").unlink(missing_ok=True)
            run = "run-" + secrets.token_hex(8)
            try:
                with LocalStack(state / run) as stack:
                    result, intent = prepare(stack)
                    private(stack.workspace / "lifecycle-evidence-private.json", result)
                    authority = f"127.0.0.1:{public_port or port}"
                    stack.config["console_allowed_host"] = authority
                    origin = "http://" + authority
                    process = start_api(
                        stack, port, "0.0.0.0" if container else "127.0.0.1", origin
                    )
                    private(
                        stack.workspace / "operator-credentials.json",
                        {
                            u: {
                                "username": u,
                                "password": v["password"],
                                "role": v["role"],
                                "scopes": v["scopes"],
                            }
                            for u, v in stack.operators.items()
                        },
                    )
                    private(
                        stack.workspace / "inspection-private.json",
                        {
                            "control_dsn": stack.config["control_dsn"],
                            "read_dsns": {u: v["dsn"] for u, v in stack.operators.items()},
                            "receiver_dsns": {n: stack.app_dsns[n] for n in ["stripe", "netsuite"]},
                            "owned_ports": [
                                int(conninfo_to_dict(stack.admin)["port"]),
                                port,
                                *(
                                    urlsplit(s["url"]).port
                                    for s in stack.config["services"].values()
                                ),
                            ],
                        },
                    )
                    private(
                        pointer, dict(run=run, origin=origin, port=port, recovery_effect=intent)
                    )
                    print(
                        json.dumps(
                            {
                                "event": "ready",
                                "url": origin,
                                "credentials": "Use the private credentials command.",
                            }
                        ),
                        flush=True,
                    )
                    while not stop.wait(1):
                        marker = state / "stop-private.json"
                        if marker.exists() and json.loads(marker.read_bytes()).get("run") == run:
                            marker.unlink()
                            break
                        if any(p.poll() is not None for p, _ in stack.processes):
                            raise RuntimeError("owned_application_process_exited")
                        with connect(stack.config["control_dsn"]) as c:
                            c.execute("SELECT 1")
                    process.terminate()
                    process.wait(timeout=30)
                private(
                    state / "shutdown.json",
                    {
                        "owned_database_and_http_stopped": True,
                        "stopped_at": datetime.now(UTC).isoformat(),
                    },
                )
            finally:
                pointer.unlink(missing_ok=True)
    finally:
        for s, handler in prior.items():
            signal.signal(s, handler)
    print(json.dumps({"event": "stopped"}), flush=True)


def inspect(state):
    record, run = current(state)
    config = json.loads((run / "inspection-private.json").read_bytes())
    observations = {}
    for name, dsn in config["read_dsns"].items():
        with connect(dsn) as c:
            observations[name] = {
                "write_permissions": c.execute(
                    "SELECT has_table_privilege(current_user,'proposals','INSERT') propose,has_table_privilege(current_user,'decisions','INSERT') decide"
                ).fetchone(),
                "restricted_role": not any(
                    c.execute(
                        "SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                    .fetchone()
                    .values()
                ),
                "outbox": c.execute(
                    "SELECT effect_id,tenant,entity,event_id,destination,status,attempts,max_attempts,payload,hash,authorization_proposal FROM outbox ORDER BY effect_id"
                ).fetchall(),
                "runs": c.execute(
                    "SELECT tenant,entity,report FROM financial_runs ORDER BY created_at"
                ).fetchall(),
                "cases": c.execute(
                    "SELECT tenant,entity,state,amount_cents FROM financial_cases ORDER BY id"
                ).fetchall(),
                "decisions": c.execute(
                    "SELECT proposal_id,result FROM decisions ORDER BY created_at"
                ).fetchall(),
                "jobs": c.execute(
                    "SELECT id,proposal_id,state,detail,fence FROM reconciliation_jobs ORDER BY created_at"
                ).fetchall(),
                "proposals": c.execute(
                    "SELECT id,state,detail FROM proposals ORDER BY created_at"
                ).fetchall(),
            }
    with connect(config["control_dsn"]) as c:
        rls = c.execute(
            "SELECT relname,relrowsecurity FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='public' AND relname IN('outbox','financial_runs','proposals','decisions') ORDER BY relname"
        ).fetchall()
    receivers = {}
    for name, dsn in config["receiver_dsns"].items():
        with connect(dsn) as c:
            receivers[name] = {
                table: c.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()
                for table in ["effects", "records", "requests"]
            }
    financial_jobs = []
    for path in sorted((run / "console-worker/jobs").glob("*/dbt/target/run_results.json")):
        result = json.loads(path.read_bytes())
        financial_jobs.append(
            {
                "job": path.parents[2].name,
                "results": [
                    {"id": r["unique_id"], "status": r["status"], "message": r.get("message")}
                    for r in result["results"]
                ],
            }
        )
    return dict(
        run=record["run"],
        roles=observations,
        rls=rls,
        receivers=receivers,
        financial_jobs=financial_jobs,
        container_memory={
            name: (Path("/sys/fs/cgroup") / name).read_text().strip()
            for name in ["memory.events", "memory.current", "memory.peak", "memory.max"]
            if (Path("/sys/fs/cgroup") / name).is_file()
        },
        source_hashes={
            p.relative_to(Path(__file__).resolve().parents[2]).as_posix(): hashlib.sha256(
                p.read_text(encoding="utf-8").encode()
            ).hexdigest()
            for folder in ["src", "dbt", "web"]
            for p in (Path(__file__).resolve().parents[2] / folder).rglob("*")
            if p.is_file()
            and p.suffix in [".py", ".sql", ".yml", ".css", ".js", ".html"]
            and not any(part in p.parts for part in ["target", "logs", "__pycache__"])
        },
        owned_ports=config["owned_ports"],
        private_modes={
            p.name: oct(p.stat().st_mode & 0o777)
            for p in [run / "operator-credentials.json", run / "console-private.json"]
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ["serve", "credentials", "inspect", "stop", "health", "lifecycle-evidence"]:
        s = sub.add_parser(command)
        s.add_argument("--state", type=Path, default=Path("/state"))
        if command == "serve":
            s.add_argument("--port", type=int, default=8582)
            s.add_argument("--container", action="store_true")
            s.add_argument("--public-port", type=int)
        if command == "credentials":
            s.add_argument(
                "--user",
                choices=["auditor", "operator", "approver", "reader_eu", "outsider"],
                default="auditor",
            )
    a = parser.parse_args()
    state = a.state.absolute()
    if a.command == "serve":
        try:
            serve(
                state,
                a.port,
                a.container,
                a.public_port or int(os.environ.get("INTEGRITY_DEMO_PORT", a.port)),
            )
        except Exception as error:
            if state.is_dir():
                private(state / "failure-private.json", {"traceback": traceback.format_exc()})
            print(
                json.dumps(
                    {
                        "event": "failed",
                        "error_type": type(error).__name__,
                        "detail": "Inspect private state; verify the port is available.",
                    }
                ),
                flush=True,
            )
            raise SystemExit(1) from None
        return
    try:
        record, run = current(state)
    except (FileNotFoundError, KeyError, ValueError):
        if a.command == "health":
            raise SystemExit(1) from None
        raise ValueError("demo_not_ready") from None
    if a.command == "health":
        try:
            r = httpx.get(
                f"http://127.0.0.1:{record['port']}/",
                headers={"Host": urlsplit(record["origin"]).netloc},
                timeout=3,
                trust_env=False,
            )
            raise SystemExit(0 if r.status_code == 200 else 1)
        except httpx.HTTPError:
            raise SystemExit(1) from None
    if a.command == "credentials":
        print(json.dumps(json.loads((run / "operator-credentials.json").read_bytes())[a.user]))
    elif a.command == "stop":
        private(state / "stop-private.json", {"run": record["run"]})
    elif a.command == "inspect":
        print(json.dumps(inspect(state), default=str))
    elif a.command == "lifecycle-evidence":
        print((run / "lifecycle-evidence-private.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
