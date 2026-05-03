"""
Delete all rows from Live Outlier Excel tables and persisted analysis results.

Order respects foreign keys. Run from project root:
  python scripts/clear_live_outlier_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.db_config import get_connection, is_configured  # noqa: E402


def main() -> None:
    if not is_configured():
        print("DATABASE_URL not set; nothing to do.")
        sys.exit(1)
    deletes = [
        "DELETE FROM live_outlier_analysis_detail",
        "DELETE FROM live_outlier_analysis_tag_summary",
        "DELETE FROM live_outlier_analysis_run",
        "DELETE FROM live_outlier_excel_observation",
        "DELETE FROM live_outlier_excel_dataset",
    ]
    with get_connection() as conn:
        with conn.cursor() as cur:
            for sql in deletes:
                cur.execute(sql)
        conn.commit()
    print("Cleared live_outlier_analysis_* and live_outlier_excel_* tables.")


if __name__ == "__main__":
    main()
