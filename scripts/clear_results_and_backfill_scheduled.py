"""
Clear persisted run/scheduled result tables, then run scheduled anomaly catch-up
until the DB is caught up to available timeseries data (respects SCHEDULED_ANOMALY_START_UTC).

Usage (from repo root):
  python scripts/clear_results_and_backfill_scheduled.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from services import db_queries  # noqa: E402
from services.db_config import get_connection, is_configured  # noqa: E402
from services.scheduled_anomaly_runner import (  # noqa: E402
    catch_up_scheduled_days,
    floor_day_utc_naive,
)


def clear_result_tables() -> None:
    """Remove scheduled live-dashboard rows and interactive anomaly/outlier runs (FKs cascade)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM scheduled_anomaly_job")
            cur.execute("DELETE FROM anomaly_run")
            cur.execute("DELETE FROM outlier_run")
        conn.commit()


def main() -> int:
    if not is_configured():
        print("DATABASE_URL is not set; cannot run.", file=sys.stderr)
        return 1

    print("Deleting scheduled + interactive result rows...")
    clear_result_tables()
    print("Done.")

    ts_id = db_queries.get_latest_timeseries_dataset_id()
    if not ts_id:
        print("No timeseries dataset in DB; skipping catch-up.", file=sys.stderr)
        return 0

    max_obs = db_queries.timeseries_dataset_max_observed_at(int(ts_id))
    target_day = floor_day_utc_naive(max_obs) if max_obs else None
    print(f"Catch-up target day (max observed): {target_day}")

    prev_last = None
    for i in range(500):
        catch_up_scheduled_days()
        last = db_queries.scheduled_max_finished_hour_bucket()
        if last is not None and last == prev_last:
            print(f"No further progress after {i + 1} catch-up round(s); stopping.")
            break
        if last is None and i > 0 and prev_last is None:
            print("No finished jobs produced; check timeseries/causal data and paths.")
            break
        prev_last = last
        if target_day and last and floor_day_utc_naive(last) >= target_day:
            print("Reached max observed day.")
            break
        if i % 10 == 0 and last:
            print(f"  … latest finished bucket: {last}")

    final_finished = db_queries.scheduled_max_finished_hour_bucket()
    final_completed = db_queries.scheduled_max_completed_hour_bucket()
    print(f"Final latest finished hour_bucket: {final_finished}")
    print(f"Final latest completed hour_bucket: {final_completed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
