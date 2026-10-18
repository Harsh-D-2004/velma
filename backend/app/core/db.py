import time

import psycopg
from psycopg_pool import ConnectionPool

from app.core.settings import settings

# Neon: autocommit, no prepared statements (pooler), 10 s connect.
_KW = {"autocommit": True, "prepare_threshold": None, "connect_timeout": 10}

pool: ConnectionPool | None = None


def connect() -> psycopg.Connection:
    try:
        return psycopg.connect(settings.database_url, **_KW)
    except psycopg.OperationalError:
        time.sleep(2)  # Neon may be waking from idle
        return psycopg.connect(settings.database_url, **_KW)


def open_pool() -> None:
    global pool
    pool = ConnectionPool(settings.database_url, min_size=0, max_size=10, kwargs=_KW,
                          check=ConnectionPool.check_connection, max_idle=300, open=False)
    pool.open()


def close_pool() -> None:
    if pool:
        pool.close()
