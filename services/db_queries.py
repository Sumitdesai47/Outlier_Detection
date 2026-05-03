"""Read paginated upload data from MySQL."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .db_config import get_connection, is_configured

PAGE_SIZE = 50

# Chunk keyset reads on live_outlier_analysis_detail (avoids huge single-result sorts / 1038).
_LIVE_OUTLIER_DETAIL_CHUNK = 50_000

# Only alphanumeric + underscore table names are browseable (SQL identifier safety).
_BROWSE_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")

# Avoid huge cells in the HTML browser.
_BROWSE_CELL_MAX_LEN = 4000


def _decode_json(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def _tag_names_list(val: Any) -> List[str]:
    d = _decode_json(val)
    if isinstance(d, list):
        return [str(x) for x in d]
    return []


def _clamp_page(page: int, total: int, per_page: int) -> int:
    page = max(1, int(page))
    if total <= 0:
        return 1
    total_pages = max(1, (total + per_page - 1) // per_page)
    return min(page, total_pages)


def count_timeseries_datasets() -> int:
    if not is_configured():
        return 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM timeseries_dataset")
            return int(cur.fetchone()[0])


def list_timeseries_datasets_page(
    page: int, per_page: int = PAGE_SIZE
) -> Tuple[List[Dict[str, Any]], int, int]:
    if not is_configured():
        return [], 0, 1
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM timeseries_dataset")
            total = int(cur.fetchone()[0])
            page = _clamp_page(page, total, per_page)
            offset = (page - 1) * per_page
            cur.execute(
                """
                SELECT id, original_filename, uploaded_at, row_count,
                       COALESCE(content_sha256, '') AS content_sha256,
                       COALESCE(JSON_LENGTH(tag_names), 0) AS num_tags
                FROM timeseries_dataset
                ORDER BY id DESC
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            for r in rows:
                if r.get("uploaded_at"):
                    r["uploaded_at"] = r["uploaded_at"].isoformat()
            return rows, total, page


def count_timeseries_observations(dataset_id: int) -> int:
    if not is_configured():
        return 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM timeseries_observation WHERE dataset_id = %s",
                (dataset_id,),
            )
            return int(cur.fetchone()[0])


def get_timeseries_dataset_meta(dataset_id: int) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, original_filename, uploaded_at, row_count, content_sha256, tag_names
                FROM timeseries_dataset WHERE id = %s
                """,
                (dataset_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "original_filename": row[1],
                "uploaded_at": row[2].isoformat() if row[2] else None,
                "row_count": row[3],
                "content_sha256": row[4] or "",
                "tag_names": _tag_names_list(row[5]),
            }


def list_timeseries_observations_page(
    dataset_id: int, page: int, per_page: int = PAGE_SIZE
) -> Tuple[List[Dict[str, Any]], int, int]:
    if not is_configured():
        return [], 0, 1
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM timeseries_observation WHERE dataset_id = %s",
                (dataset_id,),
            )
            total = int(cur.fetchone()[0])
            page = _clamp_page(page, total, per_page)
            offset = (page - 1) * per_page
            # FORCE INDEX (uq_timeseries_obs): (dataset_id, row_index, tag_name) matches ORDER BY → no filesort (1038).
            cur.execute(
                """
                SELECT id, row_index, observed_at, observed_at_raw, tag_name, value
                FROM timeseries_observation FORCE INDEX (uq_timeseries_obs)
                WHERE dataset_id = %s
                ORDER BY row_index ASC, tag_name ASC
                LIMIT %s OFFSET %s
                """,
                (dataset_id, per_page, offset),
            )
            cols = [d[0] for d in cur.description]
            out: List[Dict[str, Any]] = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                if d.get("observed_at"):
                    d["observed_at"] = d["observed_at"].isoformat()
                out.append(d)
            return out, total, page


def list_timeseries_observations_global_page(
    page: int, per_page: int = PAGE_SIZE
) -> Tuple[List[Dict[str, Any]], int, int]:
    if not is_configured():
        return [], 0, 1
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM timeseries_observation")
            total = int(cur.fetchone()[0])
            page = _clamp_page(page, total, per_page)
            offset = (page - 1) * per_page
            # ORDER BY primary key only: global sort by (dataset_id, row_index, tag) needs a full-table filesort
            # and triggers errno 1038 on large tables. Rows are shown in reverse insert order (newest first).
            cur.execute(
                """
                SELECT o.dataset_id, o.row_index, o.observed_at, o.observed_at_raw, o.tag_name, o.value
                FROM timeseries_observation o
                ORDER BY o.id DESC
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
            cols = [d[0] for d in cur.description]
            out: List[Dict[str, Any]] = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                if d.get("observed_at"):
                    d["observed_at"] = d["observed_at"].isoformat()
                out.append(d)
            return out, total, page


def count_distinct_timeseries_observation_dataset_ids() -> int:
    """Number of timeseries_dataset rows that have at least one observation (no COUNT DISTINCT scan)."""
    if not is_configured():
        return 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM timeseries_dataset d
                WHERE EXISTS (
                    SELECT 1 FROM timeseries_observation o WHERE o.dataset_id = d.id
                )
                """
            )
            r = cur.fetchone()
            return int(r[0]) if r and r[0] is not None else 0


def count_causal_datasets() -> int:
    if not is_configured():
        return 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM causal_dataset")
            return int(cur.fetchone()[0])


def list_causal_datasets_page(
    page: int, per_page: int = PAGE_SIZE
) -> Tuple[List[Dict[str, Any]], int, int]:
    if not is_configured():
        return [], 0, 1
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM causal_dataset")
            total = int(cur.fetchone()[0])
            page = _clamp_page(page, total, per_page)
            offset = (page - 1) * per_page
            cur.execute(
                """
                SELECT id, original_filename, uploaded_at,
                       COALESCE(content_sha256, '') AS content_sha256
                FROM causal_dataset
                ORDER BY id DESC
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            for r in rows:
                if r.get("uploaded_at"):
                    r["uploaded_at"] = r["uploaded_at"].isoformat()
            return rows, total, page


def list_causal_rows_page(
    dataset_id: int, page: int, per_page: int = PAGE_SIZE
) -> Tuple[List[Dict[str, Any]], int, int]:
    if not is_configured():
        return [], 0, 1
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM causal_row r
                JOIN causal_sheet s ON s.id = r.sheet_id
                WHERE s.dataset_id = %s
                """,
                (dataset_id,),
            )
            total = int(cur.fetchone()[0])
            page = _clamp_page(page, total, per_page)
            offset = (page - 1) * per_page
            cur.execute(
                """
                SELECT s.sheet_name, r.excel_row_number, r.propagation_path, r.row_payload
                FROM causal_row r
                JOIN causal_sheet s ON s.id = r.sheet_id
                WHERE s.dataset_id = %s
                ORDER BY s.sheet_name, r.excel_row_number
                LIMIT %s OFFSET %s
                """,
                (dataset_id, per_page, offset),
            )
            out: List[Dict[str, Any]] = []
            for sheet_name, excel_row, path, payload in cur.fetchall():
                out.append(
                    {
                        "sheet_name": sheet_name,
                        "excel_row_number": excel_row,
                        "propagation_path": path or "",
                        "row_payload": _decode_json(payload),
                    }
                )
            return out, total, page


def list_causal_rows_global_page(
    page: int, per_page: int = PAGE_SIZE
) -> Tuple[List[Dict[str, Any]], int, int]:
    if not is_configured():
        return [], 0, 1
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM causal_row")
            total = int(cur.fetchone()[0])
            page = _clamp_page(page, total, per_page)
            offset = (page - 1) * per_page
            # PK order avoids full-table filesort (1038) vs ORDER BY dataset/sheet/row across all causal_row.
            cur.execute(
                """
                SELECT s.dataset_id, s.sheet_name, r.excel_row_number, r.propagation_path, r.row_payload
                FROM causal_row r
                JOIN causal_sheet s ON s.id = r.sheet_id
                ORDER BY r.id DESC
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
            out: List[Dict[str, Any]] = []
            for dataset_id, sheet_name, excel_row, path, payload in cur.fetchall():
                out.append(
                    {
                        "dataset_id": dataset_id,
                        "sheet_name": sheet_name,
                        "excel_row_number": excel_row,
                        "propagation_path": path or "",
                        "row_payload": _decode_json(payload),
                    }
                )
            return out, total, page


def get_public_schema_catalog() -> List[Dict[str, Any]]:
    """List base tables in current database with columns and approximate row counts."""
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name, COALESCE(table_rows, 0)
                FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
            row_est = {r[0]: int(r[1] or 0) for r in cur.fetchall()}

            cur.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable, ordinal_position
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                ORDER BY table_name, ordinal_position
                """
            )
            by_table: Dict[str, List[Dict[str, Any]]] = {}
            for tname, cname, dtype, nullable, _pos in cur.fetchall():
                by_table.setdefault(tname, []).append(
                    {
                        "name": cname,
                        "data_type": dtype,
                        "nullable": nullable == "YES",
                    }
                )

            order = list(row_est.keys())
            out: List[Dict[str, Any]] = []
            for name in order:
                out.append(
                    {
                        "name": name,
                        "row_estimate": row_est.get(name, 0),
                        "columns": by_table.get(name, []),
                    }
                )
            return out


def get_causal_dataset_meta(dataset_id: int) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, original_filename, uploaded_at, content_sha256 FROM causal_dataset WHERE id = %s",
                (dataset_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "original_filename": row[1],
                "uploaded_at": row[2].isoformat() if row[2] else None,
                "content_sha256": (row[3] or "") if row[3] is not None else "",
            }


def get_latest_timeseries_dataset_id() -> Optional[int]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM timeseries_dataset ORDER BY id DESC LIMIT 1")
            r = cur.fetchone()
            return int(r[0]) if r else None


def timeseries_dataset_max_observed_at(dataset_id: int) -> Optional[datetime]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(observed_at) FROM timeseries_observation WHERE dataset_id = %s",
                (dataset_id,),
            )
            r = cur.fetchone()
            return r[0] if r and r[0] is not None else None


def timeseries_dataset_min_observed_at(dataset_id: int) -> Optional[datetime]:
    """Earliest observation timestamp for the dataset (UTC stored as naive in DB)."""
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MIN(observed_at) FROM timeseries_observation WHERE dataset_id = %s",
                (dataset_id,),
            )
            r = cur.fetchone()
            return r[0] if r and r[0] is not None else None


def timeseries_dataset_has_rows_in_range(
    dataset_id: int, start_inclusive: datetime, end_exclusive: datetime
) -> bool:
    if not is_configured():
        return False
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM timeseries_observation
                WHERE dataset_id = %s
                  AND observed_at >= %s
                  AND observed_at < %s
                LIMIT 1
                """,
                (dataset_id, start_inclusive, end_exclusive),
            )
            return cur.fetchone() is not None


def get_latest_causal_dataset_id() -> Optional[int]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM causal_dataset ORDER BY id DESC LIMIT 1")
            r = cur.fetchone()
            return int(r[0]) if r else None


def fetch_causal_propagation_paths(dataset_id: int) -> List[str]:
    """Distinct non-empty propagation_path values from stored causal rows."""
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT TRIM(r.propagation_path)
                FROM causal_row r
                INNER JOIN causal_sheet s ON s.id = r.sheet_id
                WHERE s.dataset_id = %s
                  AND r.propagation_path IS NOT NULL
                  AND TRIM(r.propagation_path) <> ''
                """,
                (dataset_id,),
            )
            return [str(row[0]) for row in cur.fetchall() if row and row[0]]


def list_plants_for_dashboard() -> List[Dict[str, Any]]:
    """All plants for Live Dashboard tabs, ordered by name."""
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    dataset_id,
                    plant_name,
                    timeseries_dataset_id,
                    COALESCE(causal_matrix_dataset_id, causal_dataset_id) AS causal_dataset_id
                FROM plant_dataset
                ORDER BY plant_name ASC, dataset_id ASC
                """
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def list_live_outlier_excel_datasets() -> List[Dict[str, Any]]:
    """Uploaded Excel datasets for Live Outlier (name + file metadata), newest first."""
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, dataset_name, original_filename, uploaded_at
                FROM live_outlier_excel_dataset
                ORDER BY id DESC
                """
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def live_outlier_excel_dataset_by_id(dataset_id: int) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, dataset_name, original_filename, uploaded_at
                FROM live_outlier_excel_dataset
                WHERE id = %s
                """,
                (int(dataset_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))


def live_outlier_excel_dataset_min_observed_at(dataset_id: int) -> Optional[datetime]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MIN(observed_at) FROM live_outlier_excel_observation WHERE dataset_id = %s",
                (int(dataset_id),),
            )
            r = cur.fetchone()
            return r[0] if r and r[0] is not None else None


def live_outlier_excel_dataset_max_observed_at(dataset_id: int) -> Optional[datetime]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(observed_at) FROM live_outlier_excel_observation WHERE dataset_id = %s",
                (int(dataset_id),),
            )
            r = cur.fetchone()
            return r[0] if r and r[0] is not None else None


def live_outlier_excel_distinct_observation_days(
    dataset_id: int, limit: int = 8000
) -> List[date]:
    """UTC calendar days that have at least one observation (for Live Outlier calendar, like completed days on Live Dashboard)."""
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT CAST(observed_at AS DATE) AS d
                FROM live_outlier_excel_observation
                WHERE dataset_id = %s AND observed_at IS NOT NULL
                ORDER BY d ASC
                LIMIT %s
                """,
                (int(dataset_id), int(limit)),
            )
            out: List[date] = []
            for row in cur.fetchall():
                if not row or row[0] is None:
                    continue
                v = row[0]
                if isinstance(v, datetime):
                    out.append(v.date())
                elif isinstance(v, date):
                    out.append(v)
            return out


def first_plant_dataset_id() -> Optional[int]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MIN(dataset_id) FROM plant_dataset")
            r = cur.fetchone()
            return int(r[0]) if r and r[0] is not None else None


def list_plants_with_schedule_mappings() -> List[Dict[str, Any]]:
    """Plants that have both legacy timeseries_dataset_id and causal_dataset_id set (for scheduler)."""
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    dataset_id,
                    plant_name,
                    timeseries_dataset_id,
                    COALESCE(causal_matrix_dataset_id, causal_dataset_id) AS causal_dataset_id
                FROM plant_dataset
                WHERE timeseries_dataset_id IS NOT NULL
                  AND COALESCE(causal_matrix_dataset_id, causal_dataset_id) IS NOT NULL
                ORDER BY plant_name ASC, dataset_id ASC
                """
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def find_plant_dataset_id_for_links(
    timeseries_dataset_id: Optional[int],
    causal_dataset_id: Optional[int] = None,
) -> Optional[int]:
    """Resolve plant_dataset_id by mapped dataset links, if present."""
    if not is_configured() or not timeseries_dataset_id:
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            if causal_dataset_id:
                cur.execute(
                    """
                    SELECT dataset_id
                    FROM plant_dataset
                    WHERE timeseries_dataset_id = %s
                      AND COALESCE(causal_matrix_dataset_id, causal_dataset_id) = %s
                    ORDER BY dataset_id DESC
                    LIMIT 1
                    """,
                    (timeseries_dataset_id, causal_dataset_id),
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    return int(row[0])
            cur.execute(
                """
                SELECT dataset_id
                FROM plant_dataset
                WHERE timeseries_dataset_id = %s
                ORDER BY dataset_id DESC
                LIMIT 1
                """,
                (timeseries_dataset_id,),
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else None


_SCHED_JOB_SELECT_COLS = (
    "id, hour_bucket, timeseries_dataset_id, causal_dataset_id, plant_dataset_id, status, "
    "error_message, summary, created_at, finished_at"
)


def _scheduled_job_dict_from_row(row: tuple, cur) -> Dict[str, Any]:
    cols = [d[0] for d in cur.description]
    d = dict(zip(cols, row))
    d["summary"] = _decode_json(d.get("summary"))
    return d


def scheduled_job_row_by_bucket(
    hour_bucket: datetime, plant_dataset_id: int
) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_SCHED_JOB_SELECT_COLS}
                FROM scheduled_anomaly_job
                WHERE hour_bucket = %s AND plant_dataset_id = %s
                """,
                (hour_bucket, plant_dataset_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _scheduled_job_dict_from_row(row, cur)


def scheduled_max_completed_hour_bucket() -> Optional[datetime]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(hour_bucket) FROM scheduled_anomaly_job
                WHERE status = 'completed'
                """
            )
            r = cur.fetchone()
            if not r or r[0] is None:
                return None
            return r[0]


def scheduled_max_finished_hour_bucket() -> Optional[datetime]:
    """Latest day bucket that finished (completed, skipped, or failed). Used for catch-up cursor."""
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(hour_bucket) FROM scheduled_anomaly_job
                WHERE status IN ('completed', 'skipped', 'failed')
                """
            )
            r = cur.fetchone()
            if not r or r[0] is None:
                return None
            return r[0]


def scheduled_max_processed_hour_bucket_for_plant(plant_dataset_id: int) -> Optional[datetime]:
    """
    Latest day bucket successfully handled for this plant (completed or skipped).
    Failed days are excluded so the next catch-up pass can retry them.
    """
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(hour_bucket) FROM scheduled_anomaly_job
                WHERE plant_dataset_id = %s
                  AND status IN ('completed', 'skipped')
                """,
                (int(plant_dataset_id),),
            )
            r = cur.fetchone()
            if not r or r[0] is None:
                return None
            return r[0]


def scheduled_latest_completed_job_for_plant(plant_dataset_id: int) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_SCHED_JOB_SELECT_COLS}
                FROM scheduled_anomaly_job
                WHERE status = 'completed' AND plant_dataset_id = %s
                ORDER BY hour_bucket DESC
                LIMIT 1
                """,
                (plant_dataset_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _scheduled_job_dict_from_row(row, cur)


def scheduled_latest_job_for_plant(plant_dataset_id: int) -> Optional[Dict[str, Any]]:
    """Most recent job row for this plant (any status), for UI hints when nothing completed yet."""
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_SCHED_JOB_SELECT_COLS}
                FROM scheduled_anomaly_job
                WHERE plant_dataset_id = %s
                ORDER BY hour_bucket DESC, id DESC
                LIMIT 1
                """,
                (int(plant_dataset_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _scheduled_job_dict_from_row(row, cur)


def scheduled_latest_running_hour_bucket_for_plant(plant_dataset_id: int) -> Optional[datetime]:
    """Newest running job day bucket for this plant, if any."""
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(hour_bucket)
                FROM scheduled_anomaly_job
                WHERE plant_dataset_id = %s
                  AND status = 'running'
                """,
                (int(plant_dataset_id),),
            )
            r = cur.fetchone()
            if not r or r[0] is None:
                return None
            return r[0]


def scheduled_latest_completed_job_for_plant_and_day(
    plant_dataset_id: int, day_utc: date
) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_SCHED_JOB_SELECT_COLS}
                FROM scheduled_anomaly_job
                WHERE status = 'completed'
                  AND plant_dataset_id = %s
                  AND DATE(hour_bucket) = %s
                ORDER BY hour_bucket DESC
                LIMIT 1
                """,
                (plant_dataset_id, day_utc),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _scheduled_job_dict_from_row(row, cur)


def scheduled_list_completed_days_for_plant(
    plant_dataset_id: int, limit: int = 2500
) -> List[date]:
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT DATE(hour_bucket) AS day_bucket
                FROM scheduled_anomaly_job
                WHERE status = 'completed' AND plant_dataset_id = %s
                ORDER BY day_bucket DESC
                LIMIT %s
                """,
                (plant_dataset_id, limit),
            )
            return [row[0] for row in cur.fetchall()]


def scheduled_latest_completed_job() -> Optional[Dict[str, Any]]:
    """Latest completed job for the first plant (by MIN(dataset_id)); ambiguous if multiple plants."""
    pid = first_plant_dataset_id()
    if pid is None:
        return None
    return scheduled_latest_completed_job_for_plant(pid)


def scheduled_latest_completed_job_for_day(day_utc: date) -> Optional[Dict[str, Any]]:
    """Latest completed job for the given calendar day and first plant."""
    pid = first_plant_dataset_id()
    if pid is None:
        return None
    return scheduled_latest_completed_job_for_plant_and_day(pid, day_utc)


def scheduled_job_by_id(job_id: int) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_SCHED_JOB_SELECT_COLS}
                FROM scheduled_anomaly_job
                WHERE id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _scheduled_job_dict_from_row(row, cur)


def scheduled_drift_rows_for_job(job_id: int) -> List[Dict[str, Any]]:
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rank_order, tag, drift_score
                FROM scheduled_anomaly_drift
                WHERE job_id = %s
                ORDER BY rank_order ASC
                """,
                (job_id,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def scheduled_root_rows_for_job_tag(job_id: int, tag: str) -> List[Dict[str, Any]]:
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rank_order, root_cause_tag, root_cause_score, propagation_path
                FROM scheduled_anomaly_root
                WHERE job_id = %s AND target_tag = %s
                ORDER BY rank_order ASC
                """,
                (job_id, tag),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def list_base_tables_for_browse() -> List[Dict[str, Any]]:
    """
    BASE TABLE and VIEW names in the current schema (DATABASE()), sorted by name.
    Skips names that are not simple identifiers.
    """
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TABLE_NAME, COALESCE(TABLE_ROWS, 0) AS approx_rows, COALESCE(ENGINE, '') AS engine
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
                ORDER BY TABLE_NAME
                """
            )
            cols = [d[0] for d in cur.description]
            raw = [dict(zip(cols, row)) for row in cur.fetchall()]
    out: List[Dict[str, Any]] = []
    for r in raw:
        name = r.get("TABLE_NAME") or ""
        if not _BROWSE_TABLE_NAME_RE.fullmatch(name):
            continue
        out.append(
            {
                "name": name,
                "approx_rows": int(r.get("approx_rows") or 0),
                "engine": (r.get("engine") or "") or "",
            }
        )
    return out


def _browse_format_cell(val: Any) -> str:
    """Format a column value for safe HTML text display (Jinja will escape)."""
    if val is None:
        return "—"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (dict, list)):
        s = json.dumps(val, default=str)
    elif isinstance(val, (datetime, date)):
        s = val.isoformat()
    elif isinstance(val, Decimal):
        s = format(val, "f")
    elif isinstance(val, (bytes, bytearray)):
        s = f"<binary, {len(val)} bytes>"
    else:
        s = str(val)
    if len(s) > _BROWSE_CELL_MAX_LEN:
        return s[: _BROWSE_CELL_MAX_LEN] + "…"
    return s


def browse_table_rows_page(
    table: Optional[str], page: int, per_page: int = PAGE_SIZE
) -> Tuple[List[str], List[Dict[str, str]], int, int]:
    """
    Paginated SELECT * for a whitelisted base table in the current database.

    Returns: column names, rows as dicts of display strings, total row count, clamped page.
    """
    if not table or not is_configured():
        return [], [], 0, 1
    if not _BROWSE_TABLE_NAME_RE.fullmatch(table):
        return [], [], 0, 1

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
                  AND TABLE_NAME = %s
                LIMIT 1
                """,
                (table,),
            )
            if not cur.fetchone():
                return [], [], 0, 1

            # table is whitelisted via information_schema match (alphanumeric + underscore).
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            total = int(cur.fetchone()[0])
            page = _clamp_page(page, total, per_page)
            offset = (page - 1) * per_page
            cur.execute(
                f"SELECT * FROM `{table}` LIMIT %s OFFSET %s",
                (per_page, offset),
            )
            desc = cur.description or []
            col_names = [d[0] for d in desc]
            raw_rows = cur.fetchall()
            out_rows: List[Dict[str, str]] = []
            for row in raw_rows:
                out_rows.append(
                    {col_names[i]: _browse_format_cell(row[i]) for i in range(len(col_names))}
                )
            return col_names, out_rows, total, page


def scheduled_list_completed_hour_buckets(limit: int = 720) -> List[datetime]:
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT hour_bucket FROM scheduled_anomaly_job
                WHERE status = 'completed'
                ORDER BY hour_bucket DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [row[0] for row in cur.fetchall()]


def scheduled_list_completed_days(limit: int = 365) -> List[date]:
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT DATE(hour_bucket) AS day_bucket
                FROM scheduled_anomaly_job
                WHERE status = 'completed'
                ORDER BY day_bucket DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [row[0] for row in cur.fetchall()]


def latest_live_outlier_analysis_run(dataset_id: int) -> Optional[Dict[str, Any]]:
    """Latest persisted V5 analysis for a Live Outlier Excel dataset (any status)."""
    if not is_configured():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            # MAX(id) avoids ORDER BY ... LIMIT filesort on large run tables (errno 1038).
            cur.execute(
                "SELECT MAX(id) FROM live_outlier_analysis_run WHERE dataset_id = %s",
                (int(dataset_id),),
            )
            mx = cur.fetchone()
            if not mx or mx[0] is None:
                return None
            rid = int(mx[0])
            cur.execute(
                """
                SELECT id, dataset_id, started_at, finished_at, status, error_message,
                       summary_json, artifacts_json
                FROM live_outlier_analysis_run
                WHERE id = %s
                """,
                (rid,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            out = dict(zip(cols, row))
            out["summary_json"] = _decode_json(out.get("summary_json"))
            out["artifacts_json"] = _decode_json(out.get("artifacts_json"))
            return out


def fetch_live_outlier_detail_rows_for_day(
    run_id: int, day_start: datetime, day_end_exclusive: datetime
) -> List[Dict[str, Any]]:
    """Stored detail rows for a run in ``[day_start, day_end_exclusive)`` (UTC-naive)."""
    if not is_configured():
        return []
    rid = int(run_id)
    out: List[Dict[str, Any]] = []
    cols: List[str] = []
    last_id = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            while True:
                # ORDER BY id with LIMIT: bounded sort per chunk (avoids errno 1038 on huge days).
                cur.execute(
                    """
                    SELECT id, tag_name, observed_at, actual_value, predicted_value,
                           final_class, direction, reason
                    FROM live_outlier_analysis_detail
                    WHERE run_id = %s
                      AND observed_at IS NOT NULL
                      AND observed_at >= %s
                      AND observed_at < %s
                      AND id > %s
                    ORDER BY id ASC
                    LIMIT %s
                    """,
                    (rid, day_start, day_end_exclusive, last_id, _LIVE_OUTLIER_DETAIL_CHUNK),
                )
                if not cols:
                    cols = [d[0] for d in (cur.description or [])]
                rows = cur.fetchall()
                if not rows:
                    break
                for r in rows:
                    d = dict(zip(cols, r))
                    last_id = int(d.pop("id", 0) or 0)
                    out.append(d)
                if len(rows) < _LIVE_OUTLIER_DETAIL_CHUNK:
                    break
    return out


def fetch_live_outlier_detail_rows_for_tag_time_range(
    run_id: int,
    tag: str,
    t_start: datetime,
    t_end: datetime,
) -> List[Dict[str, Any]]:
    """Detail rows for one tag between ``t_start`` and ``t_end`` inclusive (UTC-naive). Keyset-chunked."""
    if not is_configured():
        return []
    rid = int(run_id)
    tnm = str(tag)
    out: List[Dict[str, Any]] = []
    cols: List[str] = []
    last_id = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            while True:
                cur.execute(
                    """
                    SELECT id, tag_name, observed_at, actual_value, predicted_value,
                           final_class, direction, reason
                    FROM live_outlier_analysis_detail
                    WHERE run_id = %s
                      AND tag_name = %s
                      AND observed_at IS NOT NULL
                      AND observed_at >= %s
                      AND observed_at <= %s
                      AND id > %s
                    ORDER BY id ASC
                    LIMIT %s
                    """,
                    (rid, tnm, t_start, t_end, last_id, _LIVE_OUTLIER_DETAIL_CHUNK),
                )
                if not cols:
                    cols = [d[0] for d in (cur.description or [])]
                rows = cur.fetchall()
                if not rows:
                    break
                for r in rows:
                    d = dict(zip(cols, r))
                    last_id = int(d.pop("id", 0) or 0)
                    out.append(d)
                if len(rows) < _LIVE_OUTLIER_DETAIL_CHUNK:
                    break
    return out


def live_outlier_distinct_tags_for_run_day(
    run_id: int, day_start: datetime, day_end_exclusive: datetime
) -> List[str]:
    if not is_configured():
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Prefer tag_summary + EXISTS: one index lookup per tag, avoids DISTINCT sort on huge detail (1038).
            cur.execute(
                """
                SELECT t.tag_name
                FROM live_outlier_analysis_tag_summary t
                WHERE t.run_id = %s
                  AND EXISTS (
                    SELECT 1 FROM live_outlier_analysis_detail d
                    WHERE d.run_id = t.run_id
                      AND d.tag_name = t.tag_name
                      AND d.observed_at IS NOT NULL
                      AND d.observed_at >= %s
                      AND d.observed_at < %s
                    LIMIT 1
                  )
                ORDER BY t.tag_name
                """,
                (int(run_id), day_start, day_end_exclusive),
            )
            tags = [str(r[0]) for r in cur.fetchall() if r and r[0]]
            if tags:
                return tags
            cur.execute(
                "SELECT COUNT(*) FROM live_outlier_analysis_tag_summary WHERE run_id = %s",
                (int(run_id),),
            )
            n_sum = int(cur.fetchone()[0] or 0)
            if n_sum > 0:
                return []
            # Legacy runs without tag_summary: collect tag_name by id chunks (no DISTINCT sort).
            seen: set[str] = set()
            last_id = 0
            while True:
                cur.execute(
                    """
                    SELECT id, tag_name
                    FROM live_outlier_analysis_detail
                    WHERE run_id = %s
                      AND observed_at IS NOT NULL
                      AND observed_at >= %s
                      AND observed_at < %s
                      AND id > %s
                    ORDER BY id ASC
                    LIMIT %s
                    """,
                    (int(run_id), day_start, day_end_exclusive, last_id, _LIVE_OUTLIER_DETAIL_CHUNK),
                )
                rows = cur.fetchall()
                if not rows:
                    break
                for row_id, tname in rows:
                    last_id = int(row_id)
                    if tname:
                        seen.add(str(tname))
                if len(rows) < _LIVE_OUTLIER_DETAIL_CHUNK:
                    break
            return sorted(seen)


def live_outlier_tag_row_count_for_run_day(
    run_id: int, day_start: datetime, day_end_exclusive: datetime, tag: str
) -> int:
    if not is_configured():
        return 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM live_outlier_analysis_detail
                WHERE run_id = %s AND tag_name = %s
                  AND observed_at IS NOT NULL
                  AND observed_at >= %s
                  AND observed_at < %s
                """,
                (int(run_id), str(tag), day_start, day_end_exclusive),
            )
            r = cur.fetchone()
            return int(r[0]) if r and r[0] is not None else 0
