"""Local principal installation and password/session authentication; no external identity provider."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo, conninfo_to_dict

from .db import connect


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return (
        salt + ":" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200000).hex()
    )


def matches(password, encoded):
    return secrets.compare_digest(password_hash(password, encoded.split(":")[0]), encoded)


DUMMY_HASH = password_hash("not-a-user-credential", salt="0123456789abcdef")


def install(stack):
    worker = conninfo_to_dict(stack.config["control_dsn"])["user"]
    definitions = [
        ("auditor", "viewer", [("northstar", "US"), ("northstar", "EU")]),
        ("operator", "operator", [("northstar", "US")]),
        ("approver", "approver", [("northstar", "US")]),
        ("reader_eu", "viewer", [("northstar", "EU")]),
        ("outsider", "viewer", [("other", "US")]),
    ]
    if not hasattr(stack, "operators"):
        stack.operators = {}
        with psycopg.connect(stack.admin, autocommit=True) as installer:
            for username, role, scopes in definitions:
                db_role = stack.tag + "_op_" + username
                password = secrets.token_urlsafe(24)
                installer.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(db_role), sql.Literal(password)
                    )
                )
                stack.role_names.append(db_role)
                database = conninfo_to_dict(stack.admins["control"])["dbname"]
                installer.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(database), sql.Identifier(db_role)
                    )
                )
                stack.operators[username] = {
                    "username": username,
                    "role": role,
                    "scopes": scopes,
                    "password": secrets.token_urlsafe(24),
                    "db_role": db_role,
                    "dsn": make_conninfo(stack.admins["control"], user=db_role, password=password),
                }
    with connect(stack.admins["control"]) as c:
        c.execute(
            "INSERT INTO operator_scope VALUES(%s,'worker','worker','*','*') ON CONFLICT DO NOTHING",
            (worker,),
        )
        c.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION scoped(text,text) TO {}").format(
                sql.Identifier(worker)
            )
        )
        for actor in stack.operators.values():
            role = sql.Identifier(actor["db_role"])
            c.execute(
                "INSERT INTO operator_users(username,db_role,password_hash) VALUES(%s,%s,%s)",
                (actor["username"], actor["db_role"], password_hash(actor["password"])),
            )
            for tenant, entity in actor["scopes"]:
                c.execute(
                    "INSERT INTO operator_scope VALUES(%s,%s,%s,%s,%s)",
                    (actor["db_role"], actor["username"], actor["role"], tenant, entity),
                )
            c.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
            c.execute(sql.SQL("GRANT EXECUTE ON FUNCTION scoped(text,text) TO {}").format(role))
            tables = [
                "accounts",
                "mappings",
                "inbox",
                "outbox",
                "attempts",
                "exceptions",
                "audit",
                "financial_runs",
                "financial_cases",
                "financial_case_events",
                "proposals",
                "decisions",
                "reconciliation_jobs",
            ]
            c.execute(
                sql.SQL("GRANT SELECT ON {} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, tables)), role
                )
            )
            if actor["role"] == "operator":
                c.execute(sql.SQL("GRANT INSERT ON proposals TO {}").format(role))
                c.execute(sql.SQL("GRANT INSERT ON reconciliation_jobs TO {}").format(role))
            if actor["role"] == "approver":
                c.execute(sql.SQL("GRANT INSERT ON decisions TO {}").format(role))
    stack.config["operators"] = stack.operators


def authenticate(config, username, password):
    bucket = hashlib.sha256(username.encode()).hexdigest()
    with connect(config["control_dsn"]) as c:
        c.execute("INSERT INTO login_attempts(bucket) VALUES(%s) ON CONFLICT DO NOTHING", (bucket,))
        attempt = c.execute(
            "SELECT * FROM login_attempts WHERE bucket=%s FOR UPDATE", (bucket,)
        ).fetchone()
        if attempt["locked_until"] and attempt["locked_until"] > datetime.now(UTC):
            return None
        user = c.execute("SELECT * FROM operator_users WHERE username=%s", (username,)).fetchone()
        valid = matches(password, user["password_hash"] if user else DUMMY_HASH)
        if not user or not user["active"] or not valid:
            c.execute(
                "UPDATE login_attempts SET failures=failures+1,locked_until=CASE WHEN failures>=4 THEN clock_timestamp()+interval '1 minute' ELSE NULL END WHERE bucket=%s",
                (bucket,),
            )
            return None
        c.execute(
            "UPDATE login_attempts SET failures=0,locked_until=NULL WHERE bucket=%s", (bucket,)
        )
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        c.execute(
            "INSERT INTO sessions VALUES(%s,%s,%s,%s,false)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                username,
                hashlib.sha256(csrf.encode()).hexdigest(),
                datetime.now(UTC) + timedelta(minutes=30),
            ),
        )
    return token, csrf


def session(config, token):
    if not token:
        return None
    with connect(config["control_dsn"]) as c:
        row = c.execute(
            "SELECT s.*,u.active FROM sessions s JOIN operator_users u USING(username) WHERE session_hash=%s AND NOT revoked AND expires_at>clock_timestamp()",
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
    if not row or not row["active"]:
        return None
    actor = config["operators"].get(row["username"])
    return (
        {**actor, "csrf_hash": row["csrf_hash"], "session_hash": row["session_hash"]}
        if actor
        else None
    )
