"""MySQL connection from DATABASE_URL (optional). Use mysql://user:pass@host:3306/anomaly"""
from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from typing import Generator, Optional
from urllib.parse import unquote_plus, urlparse

import pymysql
from pymysql.connections import Connection
from pymysql.err import OperationalError as PyMySQLOperationalError

logger = logging.getLogger(__name__)

_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def database_url() -> Optional[str]:
    u = (os.environ.get("DATABASE_URL") or "").strip()
    if not u:
        return None
    if u.lower().startswith("mysql"):
        return u
    return None


def is_configured() -> bool:
    return database_url() is not None


def ping_mysql() -> tuple[bool, str | None]:
    """
    Try a trivial query. Returns (True, None) on success, (False, message) on failure.
    Use this to tell connection/auth issues from application SQL errors.
    """
    if not is_configured():
        return False, "DATABASE_URL is not set or does not start with mysql://"
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True, None
    except PyMySQLOperationalError as e:
        code_int: int | None = None
        if e.args:
            try:
                code_int = int(e.args[0])
            except (TypeError, ValueError):
                pass
        msg = (e.args[1] if len(e.args) > 1 else str(e)) or str(e)
        hint = ""
        if code_int in (2003, 2002):
            hint = (
                " (MySQL is not running or not listening on this host/port — start the service or "
                "`docker compose up -d`, then confirm DATABASE_URL.)"
            )
        elif code_int == 1045:
            hint = " (Wrong username or password in DATABASE_URL.)"
        elif code_int == 1049:
            hint = " (Database name in the URL does not exist — run: python scripts/init_db.py)"
        code_disp = str(code_int) if code_int is not None else "?"
        return False, f"MySQL [{code_disp}]: {msg}{hint}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def database_name_from_url(url: str) -> str:
    normalized = url.replace("mysql+pymysql://", "mysql://", 1)
    p = urlparse(normalized)
    return (p.path or "").lstrip("/") or "anomaly"


def ensure_database_exists() -> None:
    """CREATE DATABASE IF NOT EXISTS using the name from DATABASE_URL (no default DB on connect)."""
    url = database_url()
    if not url:
        return
    db_name = database_name_from_url(url)
    if not _DB_NAME_RE.fullmatch(db_name):
        raise ValueError(f"Invalid database name in DATABASE_URL: {db_name!r}")
    kw = mysql_connect_kwargs(url).copy()
    kw.pop("database", None)
    conn = pymysql.connect(**kw)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()


def mysql_connect_kwargs(url: str) -> dict:
    normalized = url.replace("mysql+pymysql://", "mysql://", 1)
    p = urlparse(normalized)
    if p.scheme != "mysql":
        raise ValueError("DATABASE_URL must be a mysql:// or mysql+pymysql:// URL")
    database = database_name_from_url(url)
    return {
        "host": p.hostname or "127.0.0.1",
        "port": p.port or 3306,
        "user": unquote_plus(p.username or ""),
        "password": unquote_plus(p.password or ""),
        "database": database,
        "charset": "utf8mb4",
        "autocommit": False,
    }


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set or is not a mysql:// URL")
    conn = pymysql.connect(**mysql_connect_kwargs(url))
    try:
        # Reduce errno 1038 (out of sort memory) on large ORDER BY / DISTINCT without changing my.cnf.
        # Override floor with MYSQL_SORT_BUFFER_MIN (bytes), clamped to a sane range.
        try:
            raw_min = (os.environ.get("MYSQL_SORT_BUFFER_MIN") or "").strip()
            floor_b = int(raw_min) if raw_min else 64 * 1024 * 1024
        except ValueError:
            floor_b = 64 * 1024 * 1024
        floor_b = max(1024 * 1024, min(floor_b, 512 * 1024 * 1024))
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SET SESSION sort_buffer_size = GREATEST(@@session.sort_buffer_size, %s)",
                    (floor_b,),
                )
            except Exception:
                logger.debug("session sort_buffer_size tweak skipped", exc_info=True)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
