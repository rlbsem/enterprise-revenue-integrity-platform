from contextlib import contextmanager
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row


@contextmanager
def connect(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3) as connection:
        connection.execute("SET statement_timeout = '12s'")
        yield connection


def migrate(dsn, name):
    body = files("integrity").joinpath("migrations", name + ".sql").read_text()
    with connect(dsn) as connection:
        connection.execute(body)


def lock(connection, *parts):
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("|".join(parts),))


def coordinated(method):
    """Hold a shared maintenance fence for the entire ingest/dispatch operation."""
    from functools import wraps

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with psycopg.connect(self.dsn, autocommit=True) as session:
            held = session.execute("SELECT pg_try_advisory_lock_shared(714290311)").fetchone()[0]
            if not held:
                from .control import Refused

                raise Refused("recovery_maintenance_in_progress")
            try:
                return method(self, *args, **kwargs)
            finally:
                session.execute("SELECT pg_advisory_unlock_shared(714290311)")

    return wrapped
