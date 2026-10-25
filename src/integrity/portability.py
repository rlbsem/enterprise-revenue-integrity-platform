"""Execute translated analytical SELECTs on real PostgreSQL; Snowflake is syntax-only."""

import hashlib
import json
import secrets
from decimal import Decimal
from pathlib import Path

import duckdb
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
import sqlglot
from sqlglot import exp


def normalize(value):
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError("fractional_financial_output")
        return int(value)
    return value


def verify(database, dbt_workspace, admin):
    manifest = json.loads((Path(dbt_workspace) / "target/manifest.json").read_text())
    results = json.loads((Path(dbt_workspace) / "target/run_results.json").read_text())
    models = [r["unique_id"] for r in results["results"] if r["unique_id"].startswith("model.")]
    name = "ri_port_" + secrets.token_hex(6)
    with psycopg.connect(admin, autocommit=True) as installer:
        installer.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    observations = []
    try:
        with (
            duckdb.connect(str(database), read_only=True) as source,
            psycopg.connect(make_conninfo(admin, dbname=name), autocommit=True) as target,
        ):
            target.execute("CREATE SCHEMA raw")
            for table in (
                "commitments",
                "records",
                "adjustments",
                "lifecycle",
                "subscription_changes",
            ):
                target.execute(
                    sql.SQL("CREATE TABLE raw.{}(body JSON)").format(sql.Identifier(table))
                )
                for row in source.execute("SELECT body FROM raw." + table).fetchall():
                    target.execute(
                        sql.SQL("INSERT INTO raw.{} VALUES(%s)").format(sql.Identifier(table)),
                        (Jsonb(json.loads(row[0])),),
                    )
            target.execute(
                "CREATE TABLE raw.reporting_period(start_date DATE,end_exclusive DATE,known_at TIMESTAMPTZ)"
            )
            for row in source.execute("SELECT * FROM raw.reporting_period").fetchall():
                target.execute("INSERT INTO raw.reporting_period VALUES(%s,%s,%s)", row)
            target.execute("CREATE TABLE raw.months(date_day DATE,valid_to DATE)")
            for row in source.execute("SELECT * FROM raw.months").fetchall():
                target.execute("INSERT INTO raw.months VALUES(%s,%s)", row)
            for unique_id in models:
                node = manifest["nodes"][unique_id]
                model = node["name"]
                ast = sqlglot.parse_one(node["compiled_code"], read="duckdb")
                for table in ast.find_all(exp.Table):
                    if table.catalog:
                        table.set("catalog", None)
                    if table.db and table.db != "raw":
                        table.set("db", exp.to_identifier("public"))
                postgres = ast.sql(dialect="postgres")
                snowflake = ast.sql(dialect="snowflake")
                sqlglot.parse_one(snowflake, read="snowflake")
                target.execute(
                    sql.SQL("CREATE TABLE {} AS ").format(sql.Identifier(model)) + sql.SQL(postgres)
                )
                left_cursor = source.execute('SELECT * FROM "' + model + '"')
                columns = [c[0] for c in left_cursor.description]
                order = ",".join(str(i) for i in range(1, len(columns) + 1))
                left = source.execute('SELECT * FROM "' + model + '" ORDER BY ' + order).fetchall()
                cursor = target.execute(
                    sql.SQL("SELECT * FROM {} ORDER BY " + order).format(sql.Identifier(model))
                )
                right = [tuple(normalize(x) for x in r) for r in cursor.fetchall()]
                if columns != [c.name for c in cursor.description] or left != right:
                    raise AssertionError("postgres_portability_mismatch:" + model)
                observations.append(
                    {
                        "model": model,
                        "row_count": len(left),
                        "all_typed_values_equal": True,
                        "postgresql_select_sha256": hashlib.sha256(postgres.encode()).hexdigest(),
                        "snowflake_sql_sha256": hashlib.sha256(snowflake.encode()).hexdigest(),
                        "snowflake": "parsed_only_not_native_execution",
                    }
                )
        return {
            "postgresql": "actual_local_execution",
            "model_comparisons": observations,
            "snowflake_native_execution": False,
        }
    finally:
        with psycopg.connect(admin, autocommit=True) as installer:
            installer.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
