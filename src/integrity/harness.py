"""Own local PostgreSQL cluster or explicitly supplied isolated test cluster, plus real HTTP workers."""

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from .db import connect, migrate
from .fixture import seed


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalStack:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        if self.workspace.exists():
            raise ValueError("fresh_workspace_required_preserve_previous_evidence")
        self.workspace.mkdir(parents=True)
        self.processes, self.database_names, self.role_names = [], [], []
        self.config, self.admins, self.app_dsns = {}, {}, {}
        self.own_cluster = False

    def start(self):
        try:
            self._database()
            self._services()
            self.events = seed(self.config, self.admins)
            self.config_path = self.workspace / "runtime.json"
            self.config_path.write_text(json.dumps(self.config))
            if os.name != "nt":
                self.config_path.chmod(0o600)
            return self
        except BaseException:
            self.stop()
            raise

    def _database(self):
        self.admin = os.environ.get("INTEGRITY_ADMIN_DSN")
        if not self.admin:
            candidate = Path(os.environ.get("PG_BIN", ""))
            initdb = shutil.which("initdb") or str(
                candidate / ("initdb.exe" if os.name == "nt" else "initdb")
            )
            self.pg_ctl = shutil.which("pg_ctl") or str(
                candidate / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")
            )
            password = secrets.token_urlsafe(32)
            pwfile = self.workspace / "pg-password"
            pwfile.write_text(password)
            if os.name != "nt":
                pwfile.chmod(0o600)
            data = self.workspace / "pgdata"
            with (self.workspace / "initdb.log").open("w") as log:
                subprocess.run(
                    [
                        initdb,
                        "-D",
                        str(data),
                        "-U",
                        "postgres",
                        "-A",
                        "scram-sha-256",
                        "--pwfile",
                        str(pwfile),
                        "--encoding=UTF8",
                        "--locale=C",
                    ],
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            pwfile.unlink()
            port = free_port()
            subprocess.run(
                [
                    self.pg_ctl,
                    "-D",
                    str(data),
                    "-l",
                    str(self.workspace / "postgres.log"),
                    "-o",
                    f"-h 127.0.0.1 -p {port} -c max_connections=40 -c shared_buffers=64MB",
                    "-w",
                    "start",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            self.own_cluster = True
            self.admin = make_conninfo(
                host="127.0.0.1", port=port, user="postgres", password=password, dbname="postgres"
            )
        self.tag = "ri_" + secrets.token_hex(5)
        with psycopg.connect(self.admin, autocommit=True) as c:
            for name in ("control", "salesforce", "stripe", "netsuite"):
                database, role = self.tag + "_" + name, self.tag + "_" + name + "_app"
                password = secrets.token_urlsafe(32)
                c.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
                self.role_names.append(role)
                c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                self.database_names.append(database)
                c.execute(
                    sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                        sql.Identifier(database)
                    )
                )
                c.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(database), sql.Identifier(role)
                    )
                )
                admin = make_conninfo(self.admin, dbname=database)
                self.admins[name] = admin
                migrate(
                    admin,
                    "control"
                    if name == "control"
                    else "crm"
                    if name == "salesforce"
                    else "receiver",
                )
                if name in ("stripe", "netsuite"):
                    migrate(admin, "financial")
                    migrate(admin, "deliveries")
                if name == "salesforce":
                    migrate(admin, "crm_changes")
                if name == "control":
                    migrate(admin, "ledger")
                with connect(admin) as db:
                    db.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                    db.execute(
                        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
                    )
                    tables = [
                        r["tablename"]
                        for r in db.execute(
                            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                        )
                    ]
                    for table in tables:
                        permissions = "SELECT"
                        if name == "salesforce" and table == "events":
                            permissions = "SELECT,INSERT"
                        if name == "salesforce" and table == "opportunities":
                            permissions = "SELECT,UPDATE"
                        if name == "control" and table not in (
                            "schema_version",
                            "accounts",
                            "mappings",
                        ):
                            permissions = (
                                "SELECT,INSERT"
                                if table in ("audit", "attempts")
                                else "SELECT,INSERT,UPDATE"
                            )
                        if name in ("stripe", "netsuite") and table != "schema_version":
                            permissions = (
                                "SELECT,INSERT,UPDATE" if table == "faults" else "SELECT,INSERT"
                            )
                        db.execute(
                            sql.SQL("GRANT " + permissions + " ON {} TO {}").format(
                                sql.Identifier(table), sql.Identifier(role)
                            )
                        )
                    db.execute(
                        sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                            sql.Identifier(role)
                        )
                    )
                app = make_conninfo(admin, user=role, password=password)
                if name == "control":
                    self.config["control_dsn"] = app
                else:
                    self.app_dsns[name] = app
                    self.config.setdefault("services", {})[name] = {
                        "token": secrets.token_urlsafe(32),
                    }

    def _services(self):
        self.injector = secrets.token_urlsafe(32)
        for name, service in self.config["services"].items():
            port = free_port()
            service["url"] = f"http://127.0.0.1:{port}"
            environment = {
                **os.environ,
                "SERVICE_KIND": name,
                "SERVICE_DSN": self.app_dsns[name],
                "SERVICE_TOKEN": service["token"],
                "INJECTOR_TOKEN": self.injector,
            }
            log = (self.workspace / (name + ".log")).open("w")
            process = subprocess.Popen(
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
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            self.processes.append((process, log))
            deadline = time.monotonic() + 25
            with httpx.Client(trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("local_service_failed_see_private_log:" + name)
                    try:
                        if client.get(service["url"] + "/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("local_service_not_ready:" + name)

    def reset(self):
        for name, admin in self.admins.items():
            with connect(admin) as c:
                tables = [
                    r["tablename"]
                    for r in c.execute(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'schema_version'"
                    )
                ]
                c.execute(
                    sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                        sql.SQL(",").join(map(sql.Identifier, tables))
                    )
                )
        self.events = seed(self.config, self.admins)

    def stop(self):
        for process, log in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            log.close()
        if self.own_cluster:
            subprocess.run(
                [self.pg_ctl, "-D", str(self.workspace / "pgdata"), "-m", "fast", "-w", "stop"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            self.own_cluster = False
        elif getattr(self, "admin", None):
            # Only exact random test DB/role names created by this object; never general cleanup.
            with psycopg.connect(self.admin, autocommit=True) as c:
                for database in self.database_names:
                    c.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            sql.Identifier(database)
                        )
                    )
                for role in self.role_names:
                    c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
        self.processes = []

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()
