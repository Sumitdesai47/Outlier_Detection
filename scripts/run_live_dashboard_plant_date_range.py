"""
Run live-dashboard daily processing (one scheduled day bucket per calendar day) for a single plant
over an inclusive UTC date range.

  python scripts/run_live_dashboard_plant_date_range.py --plant 2 --from 2024-05-01 --to 2024-06-30

Uses the same logic as the in-app scheduler: existing completed/skipped rows are left as-is;
failed rows are retried; missing observation days become skipped jobs.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from services import db_queries  # noqa: E402
from services.db_config import is_configured  # noqa: E402
from services.db_repository import apply_schema_if_needed  # noqa: E402
from services.scheduled_anomaly_runner import _run_single_scheduled_day_for_plant  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(description="Run live dashboard jobs for a plant and date range (UTC).")
    p.add_argument("--plant", type=int, required=True, help="plant_dataset.dataset_id")
    p.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", required=True, metavar="YYYY-MM-DD")
    args = p.parse_args()

    if not is_configured():
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1

    try:
        start = datetime.fromisoformat(args.date_from.strip()).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = datetime.fromisoformat(args.date_to.strip()).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    except ValueError as e:
        print(f"Invalid date: {e}", file=sys.stderr)
        return 1

    if end < start:
        print("--to must be on or after --from", file=sys.stderr)
        return 1

    apply_schema_if_needed()
    plants = db_queries.list_plants_with_schedule_mappings()
    row = next((x for x in plants if int(x["dataset_id"]) == int(args.plant)), None)
    if not row:
        print(
            f"No mapped plant with dataset_id={args.plant} "
            "(need timeseries_dataset_id and causal on plant_dataset).",
            file=sys.stderr,
        )
        return 1

    pid = int(row["dataset_id"])
    ts_id = int(row["timeseries_dataset_id"])
    causal_id = int(row["causal_dataset_id"])
    print(f"Plant {pid} ({row.get('plant_name', '')!r}) ts={ts_id} causal={causal_id}")
    print(f"UTC days {start.date()} .. {end.date()} (inclusive)")

    d = start
    n = 0
    ran = 0
    while d <= end:
        n += 1
        try:
            did = _run_single_scheduled_day_for_plant(d, pid, ts_id, causal_id)
            if did:
                ran += 1
                logger.info("%s started/finished job", d.date())
            else:
                logger.info("%s skipped (already completed or young running)", d.date())
        except Exception:
            logger.exception("Day %s failed", d.date())
        d += timedelta(days=1)

    print(f"Done. Calendar days iterated: {n}, jobs started this run: {ran}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
