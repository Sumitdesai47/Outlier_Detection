"""Apply db/schema/*.sql in order using DATABASE_URL from the environment."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from services.db_config import database_url, ensure_database_exists, get_connection  # noqa: E402


def main() -> int:
    url = database_url()
    if not url:
        print(
            "Set DATABASE_URL, e.g. mysql://user:pass@127.0.0.1:3306/anomaly",
            file=sys.stderr,
        )
        return 1
    from services.db_repository import _sql_statements  # noqa: E402

    paths = sorted(ROOT.glob("db/schema/*.sql"))
    if not paths:
        print("No SQL files under db/schema/", file=sys.stderr)
        return 1
    ensure_database_exists()
    with get_connection() as conn:
        with conn.cursor() as cur:
            for path in paths:
                sql = path.read_text(encoding="utf-8")
                for stmt in _sql_statements(sql):
                    cur.execute(stmt)
                print(f"Applied: {path.name}")
    print("Schema applied OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
