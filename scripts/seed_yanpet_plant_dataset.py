"""
Ensure plant_dataset row: dataset_id=2, plant_name='Yanpet OLF1'.

Default: direct upsert (INSERT ... ON DUPLICATE KEY UPDATE).
Optional: --conditional  only insert when time_series_data and causal_data
           already have rows for dataset_id=2 and id=2 is missing in plant_dataset.

Usage (from repo root, with DATABASE_URL set):
  python scripts/seed_yanpet_plant_dataset.py
  python scripts/seed_yanpet_plant_dataset.py --conditional

Raw SQL: see db/insert_plant_yanpet_olf1.sql
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv()

from services.plant_dataset_upload import (
    seed_plant_yanpet_olf1_if_data_exists,
    upsert_plant_yanpet_dataset_2,
)


def main() -> None:
    p = argparse.ArgumentParser(description="Seed plant_dataset Yanpet OLF1 / dataset_id 2")
    p.add_argument(
        "--conditional",
        action="store_true",
        help="Insert only if both child tables have data for dataset_id=2 and plant row missing",
    )
    args = p.parse_args()

    if args.conditional:
        out = seed_plant_yanpet_olf1_if_data_exists()
        print(out["message"])
        sys.exit(0 if out.get("success") else 1)

    out = upsert_plant_yanpet_dataset_2()
    print(out["message"])
    sys.exit(0 if out.get("success") else 1)


if __name__ == "__main__":
    main()
