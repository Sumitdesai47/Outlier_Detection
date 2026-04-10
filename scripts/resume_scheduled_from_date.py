"""
Drop scheduled live-dashboard rows from a UTC calendar day onward, then run catch-up.
Use when jobs are stuck (e.g. stale running) or you want to replay from a day.

  python scripts/resume_scheduled_from_date.py
  python scripts/resume_scheduled_from_date.py --from 2024-11-21

This process temporarily sets SCHEDULED_ANOMALY_START_UTC to --from so catch-up does not
walk years of empty history when no finished job row exists yet.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from services import db_queries  # noqa: E402
from services.db_config import is_configured  # noqa: E402
from services.db_repository import delete_scheduled_jobs_from_hour_bucket_onwards  # noqa: E402
from services.scheduled_anomaly_runner import (  # noqa: E402
    catch_up_scheduled_days,
    floor_day_utc_naive,
)


def main() -> int:
    p = argparse.ArgumentParser(description="Reset scheduled jobs from a UTC day and catch up.")
    p.add_argument(
        "--from",
        dest="from_date",
        default="2022-08-12",
        metavar="YYYY-MM-DD",
        help="Delete jobs with hour_bucket on or after this UTC midnight (default: 2022-08-12)",
    )
    args = p.parse_args()
    if not is_configured():
        print("DATABASE_URL is not set; cannot run.", file=sys.stderr)
        return 1

    day = datetime.fromisoformat(args.from_date.strip())
    day = day.replace(hour=0, minute=0, second=0, microsecond=0)
    os.environ["SCHEDULED_ANOMALY_START_UTC"] = day.strftime("%Y-%m-%dT00:00:00")
    print(f"SCHEDULED_ANOMALY_START_UTC (this run) = {os.environ['SCHEDULED_ANOMALY_START_UTC']}")

    n = delete_scheduled_jobs_from_hour_bucket_onwards(day)
    print(f"Deleted {n} scheduled_anomaly_job row(s) with hour_bucket >= {day}.")

    plants = db_queries.list_plants_with_schedule_mappings()
    ts_ids = [int(p["timeseries_dataset_id"]) for p in plants] if plants else []
    if not ts_ids:
        ts_one = db_queries.get_latest_timeseries_dataset_id()
        ts_ids = [int(ts_one)] if ts_one else []
    if not ts_ids:
        print("No timeseries dataset; skipping catch-up.", file=sys.stderr)
        return 0

    max_obs = None
    for tid in ts_ids:
        m = db_queries.timeseries_dataset_max_observed_at(tid)
        if m is not None and (max_obs is None or m > max_obs):
            max_obs = m
    target_day = floor_day_utc_naive(max_obs) if max_obs else None
    print(f"Catch-up target day (max observed): {target_day}")

    prev_last = None
    for i in range(500):
        catch_up_scheduled_days()
        last = db_queries.scheduled_max_finished_hour_bucket()
        if last is not None and last == prev_last:
            print(f"No further progress after {i + 1} catch-up round(s); stopping.")
            break
        prev_last = last
        if target_day and last and floor_day_utc_naive(last) >= target_day:
            print("Reached max observed day.")
            break
        if i % 10 == 0 and last:
            print(f"  … latest finished bucket: {last}")

    print(f"Latest finished hour_bucket: {db_queries.scheduled_max_finished_hour_bucket()}")
    print(f"Latest completed hour_bucket: {db_queries.scheduled_max_completed_hour_bucket()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
