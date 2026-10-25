from contextlib import contextmanager
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row


@contextmanager
def connect(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        connection.execute("SET statement_timeout = '12s'")
        yield connection


def migrate(dsn, name):
    body = files("integrity").joinpath("migrations", name + ".sql").read_text()
    with connect(dsn) as connection:
        connection.execute(body)


def lock(connection, *parts):
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("|".join(parts),))
