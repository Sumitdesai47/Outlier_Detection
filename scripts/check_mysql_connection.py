"""Print whether DATABASE_URL can connect to MySQL (from project root .env)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> int:
    import os

    raw = (os.environ.get("DATABASE_URL") or "").strip()
    if not raw or not raw.lower().startswith("mysql"):
        print("DATABASE_URL is missing or not mysql:// — copy .env.example to .env and set it.", file=sys.stderr)
        return 1

    from urllib.parse import urlparse

    p = urlparse(raw.replace("mysql+pymysql://", "mysql://", 1))
    host = p.hostname or "127.0.0.1"
    port = p.port or 3306
    db = (p.path or "").lstrip("/") or "anomaly"
    user = p.username or "(empty)"

    print(f"Trying: user={user!s} host={host} port={port} database={db!r}")

    try:
        import pymysql
        from services.db_config import mysql_connect_kwargs

        conn = pymysql.connect(**mysql_connect_kwargs(raw))
        conn.close()
    except pymysql.err.OperationalError as e:
        code = e.args[0] if e.args else None
        print(f"OperationalError ({code}): {e.args[1] if len(e.args) > 1 else e}", file=sys.stderr)
        if code == 2003:
            print(
                "\nConnection refused — MySQL is not accepting TCP connections on that host/port.\n"
                "  • Start MySQL (Windows: Services → MySQL, or `net start MySQL80`).\n"
                "  • Or run: docker compose up -d   (then use mysql://anomaly:anomaly@127.0.0.1:3306/anomaly)\n"
                "  • Confirm nothing else is blocking port 3306.",
                file=sys.stderr,
            )
        elif code == 1045:
            print(
                "\nAccess denied — wrong user/password in DATABASE_URL.\n"
                "  • Fix .env to match your MySQL user, or see db/MYSQL.md.",
                file=sys.stderr,
            )
        elif code == 1049:
            print(
                "\nUnknown database — create it: CREATE DATABASE anomaly; or run python scripts/init_db.py",
                file=sys.stderr,
            )
        return 1
    except Exception as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print("OK: connection succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
