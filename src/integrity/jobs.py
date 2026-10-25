"""Durable, read-only financial rechecks after approved effects. Failed gates retain prior releases."""

from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .contracts import digest
from .db import connect
from .finance import extract, load, build, publish, oracle, oracle_period
from .ledger import record


def schedule_approved(controller):
    with connect(controller.dsn) as c:
        c.execute(
            "INSERT INTO reconciliation_jobs(tenant,entity,proposal_id,reason) SELECT tenant,entity,id,'Verified approved recovery' FROM proposals WHERE state='executed' ON CONFLICT DO NOTHING"
        )


def run_one(controller, workspace):
    with psycopg.connect(controller.dsn, autocommit=True, row_factory=dict_row) as session:
        if not session.execute(
            "SELECT pg_try_advisory_lock(hashtextextended('financial-recheck',7)) AS locked"
        ).fetchone()["locked"]:
            return
        try:
            job = session.execute(
                "SELECT * FROM reconciliation_jobs WHERE state IN ('queued','running') ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not job:
                return
            claim = session.execute(
                "UPDATE reconciliation_jobs SET state='running',fence=fence+1,updated_at=clock_timestamp() WHERE id=%s RETURNING fence",
                (job["id"],),
            ).fetchone()["fence"]
            work = Path(workspace) / "jobs" / str(job["id"])
            try:
                snapshot = extract(controller, [(job["tenant"], job["entity"])])
                database = work / "warehouse.duckdb"
                load(database, snapshot)
                rows, gates = build(database, work / "dbt")
                if rows != oracle(snapshot) or gates["period_rows"] != oracle_period(snapshot):
                    raise ValueError("independent_oracle_mismatch")
                root = Path(workspace) / "releases" / digest((job["tenant"], job["entity"]))
                release = publish(
                    root, snapshot, rows, extract(controller, [(job["tenant"], job["entity"])])
                )
                record(controller, snapshot, release)
                session.execute(
                    "UPDATE reconciliation_jobs SET state='verified',detail='dbt_and_independent_oracle_passed',source_hash=%s,updated_at=clock_timestamp() WHERE id=%s AND fence=%s",
                    (release["source_hash"], job["id"], claim),
                )
                with connect(controller.dsn) as c:
                    controller.audit(
                        c,
                        job,
                        "financial_recheck_verified",
                        str(job["id"]),
                        {"source_hash": release["source_hash"]},
                    )
            except Exception as exc:
                detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                session.execute(
                    "UPDATE reconciliation_jobs SET state='failed',detail=%s,updated_at=clock_timestamp() WHERE id=%s AND fence=%s",
                    (detail, job["id"], claim),
                )
                with connect(controller.dsn) as c:
                    controller.audit(
                        c, job, "financial_recheck_failed", str(job["id"]), {"reason": detail}
                    )
        finally:
            session.execute("SELECT pg_advisory_unlock(hashtextextended('financial-recheck',7))")
