"""
Delete Live Outlier Excel dataset(s) and cascaded rows (observations, analysis, details).

  python scripts/delete_live_outlier_excel_dataset.py --dataset-name "Yanpet Furnace Data"
  python scripts/delete_live_outlier_excel_dataset.py --id 1 --name "Yanpet Furnace Data"

Loads DATABASE_URL from .env in the project root (same as init_db).
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


def main() -> None:
    p = argparse.ArgumentParser(description="Delete live_outlier_excel_dataset row(s) and cascaded data.")
    p.add_argument("--id", type=int, default=None, help="live_outlier_excel_dataset.id")
    p.add_argument(
        "--name",
        type=str,
        default="",
        help="With --id: require dataset_name to match exactly before delete.",
    )
    p.add_argument(
        "--dataset-name",
        type=str,
        default="",
        dest="dataset_name",
        help="Delete all datasets with this exact dataset_name (after trim).",
    )
    args = p.parse_args()

    if not args.id and not (args.dataset_name or "").strip():
        p.error("Provide --id and optional --name, or --dataset-name.")

    if not is_configured():
        print("DATABASE_URL not set; add mysql://... to .env or the environment.", file=sys.stderr)
        sys.exit(1)

    with get_connection() as conn:
        with conn.cursor() as cur:
            if args.dataset_name.strip():
                needle = args.dataset_name.strip()
                cur.execute(
                    """
                    SELECT id, dataset_name FROM live_outlier_excel_dataset
                    WHERE dataset_name = %s
                    ORDER BY id
                    """,
                    (needle,),
                )
                rows = cur.fetchall()
                if not rows:
                    cur.execute(
                        """
                        SELECT id, dataset_name FROM live_outlier_excel_dataset
                        WHERE LOWER(TRIM(dataset_name)) = LOWER(%s)
                        ORDER BY id
                        """,
                        (needle,),
                    )
                    rows = cur.fetchall()
                if not rows:
                    print(f"No live_outlier_excel_dataset with name matching {needle!r}.", file=sys.stderr)
                    sys.exit(2)
                deleted = 0
                for rid, ds_name in rows:
                    cur.execute("DELETE FROM live_outlier_excel_dataset WHERE id = %s", (int(rid),))
                    deleted += cur.rowcount
                    print(f"Deleted id={rid} ({ds_name!r}).")
                conn.commit()
                print(f"Done. Removed {deleted} dataset row(s); cascaded child rows removed by FK.")
                return

            cur.execute(
                "SELECT id, dataset_name FROM live_outlier_excel_dataset WHERE id = %s",
                (int(args.id),),
            )
            row = cur.fetchone()
            if not row:
                print(f"No row with id={args.id}.", file=sys.stderr)
                sys.exit(2)
            _pk, ds_name = row[0], str(row[1] or "")
            if args.name.strip():
                if ds_name != args.name.strip():
                    print(
                        f"Refusing delete: dataset_name mismatch.\n"
                        f"  Expected: {args.name.strip()!r}\n"
                        f"  Actual:   {ds_name!r}",
                        file=sys.stderr,
                    )
                    sys.exit(3)
            cur.execute(
                "DELETE FROM live_outlier_excel_dataset WHERE id = %s",
                (int(args.id),),
            )
            n = cur.rowcount
        conn.commit()

    print(f"Deleted live_outlier_excel_dataset id={args.id} ({n} row). Cascaded child rows removed by FK.")


if __name__ == "__main__":
    main()
