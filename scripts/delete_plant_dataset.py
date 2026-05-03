"""
Delete one plant_dataset row and all related application data.

Usage:
  python scripts/delete_plant_dataset.py 3

Removes:
  - scheduled_anomaly_job (and drift/root via FK cascade) for this plant
  - anomaly_run / outlier_run (and children) where plant_dataset_id matches
  - time_series_data and causal_data rows for this plant_dataset.dataset_id
  - plant_dataset row
  - timeseries_dataset (+ observations) and causal_dataset (+ sheets/rows) if
    no other plant_dataset row still references those ids
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from services.db_config import get_connection, is_configured  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Delete plant_dataset and related data.")
    p.add_argument("dataset_id", type=int, help="plant_dataset.dataset_id")
    args = p.parse_args()
    pid = args.dataset_id

    if not is_configured():
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT dataset_id, plant_name, timeseries_dataset_id, causal_dataset_id, "
                "causal_matrix_dataset_id FROM plant_dataset WHERE dataset_id = %s",
                (pid,),
            )
            row = cur.fetchone()
            if not row:
                print(f"No plant_dataset row with dataset_id={pid}.")
                return 1
            _did, pname, ts_id, causal_id, causal_matrix_id = row
            ts_id = int(ts_id) if ts_id is not None else None
            causal_id = int(causal_id) if causal_id is not None else None
            cm_id = int(causal_matrix_id) if causal_matrix_id is not None else None
            print(
                f"Deleting plant dataset_id={pid} ({pname!r}), "
                f"ts={ts_id}, causal={causal_id}, causal_matrix={cm_id}"
            )

            cur.execute(
                "DELETE FROM scheduled_anomaly_job WHERE plant_dataset_id = %s", (pid,)
            )
            print(f"  scheduled_anomaly_job: {cur.rowcount} rows")

            cur.execute("DELETE FROM anomaly_run WHERE plant_dataset_id = %s", (pid,))
            print(f"  anomaly_run: {cur.rowcount} rows")

            cur.execute("DELETE FROM outlier_run WHERE plant_dataset_id = %s", (pid,))
            print(f"  outlier_run: {cur.rowcount} rows")

            cur.execute("DELETE FROM time_series_data WHERE dataset_id = %s", (pid,))
            print(f"  time_series_data: {cur.rowcount} rows")

            cur.execute("DELETE FROM causal_data WHERE dataset_id = %s", (pid,))
            print(f"  causal_data: {cur.rowcount} rows")

            cur.execute("DELETE FROM plant_dataset WHERE dataset_id = %s", (pid,))
            print(f"  plant_dataset: {cur.rowcount} rows")

            # Orphan datasets: drop if no plant references them (process causal id once)
            seen_causal: set[int] = set()
            for label, col, did in (
                ("timeseries_dataset", "timeseries_dataset_id", ts_id),
                ("causal_dataset", "causal_dataset_id", causal_id),
                ("causal_dataset", "causal_matrix_dataset_id", cm_id),
            ):
                if did is None:
                    continue
                if col in ("causal_dataset_id", "causal_matrix_dataset_id"):
                    if did in seen_causal:
                        continue
                    seen_causal.add(did)
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM plant_dataset
                        WHERE causal_dataset_id = %s OR causal_matrix_dataset_id = %s
                        """,
                        (did, did),
                    )
                else:
                    cur.execute(
                        "SELECT COUNT(*) FROM plant_dataset WHERE timeseries_dataset_id = %s",
                        (did,),
                    )
                n = int(cur.fetchone()[0])
                if n > 0:
                    print(f"  keep {label} id={did} ({n} plant(s) still reference)")
                    continue
                if "timeseries" in label:
                    cur.execute("DELETE FROM timeseries_dataset WHERE id = %s", (did,))
                else:
                    cur.execute("DELETE FROM causal_dataset WHERE id = %s", (did,))
                print(f"  deleted {label} id={did}: {cur.rowcount} rows")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
