"""
Persist uploads and run results to MySQL when DATABASE_URL is set (mysql://...).
Same file content (SHA-256) is not inserted twice — existing dataset id is reused.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from pymysql.err import IntegrityError as PyMySQLIntegrityError, OperationalError as PyMySQLOperationalError

from .causal_service import _find_propagation_path_column
from .db_config import ensure_database_exists, get_connection, is_configured
from .time_series_utils import load_wide_time_series_xlsx

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_schema_applied = False


def _j(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _schema_sql_files() -> List[Path]:
    return sorted(_ROOT.glob("db/schema/*.sql"))


def _sql_statements(sql_text: str) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        current.append(line)
        if stripped.endswith(";"):
            block = "\n".join(current).strip().rstrip(";").strip()
            if block:
                chunks.append(block)
            current = []
    if current:
        block = "\n".join(current).strip().rstrip(";").strip()
        if block:
            chunks.append(block)
    return chunks


def _executemany_chunked(
    cur: Any, sql: str, rows: List[Tuple[Any, ...]], chunk_size: int = 2000
) -> None:
    for i in range(0, len(rows), chunk_size):
        cur.executemany(sql, rows[i : i + chunk_size])


def apply_schema_if_needed() -> None:
    """Run all db/schema/*.sql files in order (idempotent DDL)."""
    global _schema_applied
    if _schema_applied or not is_configured():
        return
    ensure_database_exists()
    paths = _schema_sql_files()
    if not paths:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            for path in paths:
                if not path.is_file():
                    continue
                sql_text = path.read_text(encoding="utf-8")
                for stmt in _sql_statements(sql_text):
                    cur.execute(stmt)
    _schema_applied = True


def _json_safe_cell(v: Any) -> Any:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def persist_timeseries_xlsx(file_path: str, original_filename: str) -> Optional[int]:
    """Load wide XLSX and store dataset + long-form observations. Returns dataset id."""
    if not is_configured():
        return None
    try:
        apply_schema_if_needed()
        content_hash = _sha256_file(file_path)
    except Exception as e:
        logger.exception("timeseries hash/schema failed: %s", e)
        return None

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM timeseries_dataset WHERE content_sha256 = %s",
                    (content_hash,),
                )
                hit = cur.fetchone()
                if hit:
                    logger.info(
                        "timeseries dedupe: reusing dataset id=%s hash=%s...",
                        hit[0],
                        content_hash[:12],
                    )
                    return int(hit[0])
    except Exception as e:
        logger.exception("timeseries dedupe lookup: %s", e)
        return None

    try:
        df = load_wide_time_series_xlsx(file_path, timestamp_col_name="Timestamp")
    except Exception as e:
        logger.exception("timeseries load failed: %s", e)
        return None

    tag_cols = [c for c in df.columns if c not in ("Timestamp", "Timestamp_raw")]
    tag_names = tag_cols
    row_count = len(df)

    observations: List[tuple] = []
    for row_idx, (_, row) in enumerate(df.iterrows()):
        ts = row.get("Timestamp")
        ts_out = None
        if ts is not None and not pd.isna(ts):
            ts_parsed = pd.Timestamp(ts)
            if ts_parsed.tzinfo is None:
                ts_out = ts_parsed.to_pydatetime()
            else:
                ts_out = ts_parsed.tz_convert("UTC").to_pydatetime()
        raw_ts = row.get("Timestamp_raw")
        raw_ts_s = None if raw_ts is None or pd.isna(raw_ts) else str(raw_ts)
        for tag in tag_cols:
            val = row.get(tag)
            num = None if val is None or (isinstance(val, float) and pd.isna(val)) else float(val)
            observations.append((row_idx, ts_out, raw_ts_s, str(tag), num))

    obs_sql = """
        INSERT INTO timeseries_observation
            (dataset_id, row_index, observed_at, observed_at_raw, tag_name, value)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            observed_at = VALUES(observed_at),
            observed_at_raw = VALUES(observed_at_raw),
            value = VALUES(value)
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO timeseries_dataset
                            (original_filename, timestamp_column, tag_names, row_count, meta, content_sha256)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            original_filename or Path(file_path).name,
                            "Timestamp",
                            _j(tag_names),
                            row_count,
                            _j({"source": "wide_xlsx_upload"}),
                            content_hash,
                        ),
                    )
                    dataset_id = int(cur.lastrowid)
                except PyMySQLIntegrityError:
                    cur.execute(
                        "SELECT id FROM timeseries_dataset WHERE content_sha256 = %s",
                        (content_hash,),
                    )
                    ex = cur.fetchone()
                    if ex:
                        logger.info("timeseries dedupe (race): reusing dataset id=%s", ex[0])
                        return int(ex[0])
                    raise

                obs_rows = [
                    (dataset_id, r, t, tr, tn, v) for r, t, tr, tn, v in observations
                ]
                _executemany_chunked(cur, obs_sql, obs_rows, chunk_size=2000)
        return int(dataset_id)
    except PyMySQLIntegrityError:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM timeseries_dataset WHERE content_sha256 = %s",
                        (content_hash,),
                    )
                    ex = cur.fetchone()
                    if ex:
                        logger.info("timeseries dedupe (race): reusing dataset id=%s", ex[0])
                        return int(ex[0])
        except Exception as e:
            logger.exception("timeseries dedupe after conflict: %s", e)
        return None
    except Exception as e:
        logger.exception("persist_timeseries_xlsx: %s", e)
        return None


def persist_causal_xlsx(file_path: str, original_filename: str) -> Optional[int]:
    """Store causal workbook: each sheet as causal_sheet + causal_row rows."""
    if not is_configured():
        return None
    try:
        apply_schema_if_needed()
        content_hash = _sha256_file(file_path)
        xl = pd.ExcelFile(file_path)
    except Exception as e:
        logger.exception("causal xlsx open/hash failed: %s", e)
        return None

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM causal_dataset WHERE content_sha256 = %s",
                    (content_hash,),
                )
                hit = cur.fetchone()
                if hit:
                    logger.info(
                        "causal dedupe: reusing dataset id=%s hash=%s...",
                        hit[0],
                        content_hash[:12],
                    )
                    return int(hit[0])
    except Exception as e:
        logger.exception("causal dedupe lookup: %s", e)
        return None

    row_sql = """
        INSERT INTO causal_row (sheet_id, excel_row_number, propagation_path, row_payload)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            propagation_path = VALUES(propagation_path),
            row_payload = VALUES(row_payload)
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO causal_dataset (original_filename, meta, content_sha256)
                        VALUES (%s, %s, %s)
                        """,
                        (
                            original_filename or Path(file_path).name,
                            _j({"sheets": xl.sheet_names}),
                            content_hash,
                        ),
                    )
                    dataset_id = int(cur.lastrowid)
                except PyMySQLIntegrityError:
                    cur.execute(
                        "SELECT id FROM causal_dataset WHERE content_sha256 = %s",
                        (content_hash,),
                    )
                    ex = cur.fetchone()
                    if ex:
                        logger.info("causal dedupe (race): reusing dataset id=%s", ex[0])
                        return int(ex[0])
                    raise

                for sheet_name in xl.sheet_names:
                    df = pd.read_excel(file_path, sheet_name=sheet_name)
                    path_col = None
                    try:
                        path_col = _find_propagation_path_column(df)
                    except Exception:
                        path_col = df.columns[0] if len(df.columns) else None

                    cur.execute(
                        """
                        INSERT INTO causal_sheet (dataset_id, sheet_name, row_count)
                        VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE row_count = VALUES(row_count)
                        """,
                        (dataset_id, str(sheet_name), len(df)),
                    )
                    cur.execute(
                        "SELECT id FROM causal_sheet WHERE dataset_id = %s AND sheet_name = %s",
                        (dataset_id, str(sheet_name)),
                    )
                    sheet_row = cur.fetchone()
                    if not sheet_row:
                        continue
                    sheet_id = int(sheet_row[0])

                    rows_buf: List[tuple] = []
                    for i, (_, row) in enumerate(df.iterrows(), start=2):
                        payload = {str(k): _json_safe_cell(row[k]) for k in df.columns}
                        ptxt = None
                        if path_col is not None and path_col in df.columns:
                            v = row.get(path_col)
                            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                                ptxt = str(v).strip() or None
                        rows_buf.append((sheet_id, i, ptxt, _j(payload)))

                    if rows_buf:
                        _executemany_chunked(cur, row_sql, rows_buf, chunk_size=500)

        return int(dataset_id)
    except PyMySQLIntegrityError:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM causal_dataset WHERE content_sha256 = %s",
                        (content_hash,),
                    )
                    ex = cur.fetchone()
                    if ex:
                        logger.info("causal dedupe (race): reusing dataset id=%s", ex[0])
                        return int(ex[0])
        except Exception as e:
            logger.exception("causal dedupe after conflict: %s", e)
        return None
    except Exception as e:
        logger.exception("persist_causal_xlsx: %s", e)
        return None


def persist_anomaly_run(
    *,
    result_session_uuid: str,
    timeseries_dataset_id: Optional[int],
    causal_dataset_id: Optional[int],
    historic_ratio: float,
    lookback_months: int,
    top_k_drift: int,
    summary: Dict[str, Any],
    top_drift_rows: List[Dict[str, Any]],
) -> Optional[int]:
    if not is_configured():
        return None
    drift_sql = """
        INSERT INTO anomaly_drift_result (run_id, rank_order, tag, drift_score)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            tag = VALUES(tag),
            drift_score = VALUES(drift_score)
    """
    try:
        apply_schema_if_needed()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO anomaly_run (
                        result_session_uuid, timeseries_dataset_id, causal_dataset_id,
                        historic_ratio, lookback_months, top_k_drift, summary, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'completed')
                    """,
                    (
                        result_session_uuid,
                        timeseries_dataset_id,
                        causal_dataset_id,
                        historic_ratio,
                        lookback_months,
                        top_k_drift,
                        _j(summary),
                    ),
                )
                run_id = int(cur.lastrowid)

                drift_tuples = [
                    (run_id, i, str(r.get("Tag", "")), r.get("Drift_Score"))
                    for i, r in enumerate(top_drift_rows)
                ]
                if drift_tuples:
                    _executemany_chunked(cur, drift_sql, drift_tuples, chunk_size=500)
        return int(run_id)
    except Exception as e:
        logger.exception("persist_anomaly_run: %s", e)
        return None


def persist_anomaly_roots(
    *,
    result_session_uuid: str,
    target_tag: str,
    rows: List[Dict[str, Any]],
) -> None:
    if not is_configured() or not rows:
        return
    insert_sql = """
        INSERT INTO anomaly_root_cause_result
            (run_id, target_tag, rank_order, root_cause_tag, root_cause_score, propagation_path)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    try:
        apply_schema_if_needed()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM anomaly_run WHERE result_session_uuid = %s",
                    (result_session_uuid,),
                )
                one = cur.fetchone()
                if not one:
                    return
                run_id = one[0]
                cur.execute(
                    "DELETE FROM anomaly_root_cause_result WHERE run_id = %s AND target_tag = %s",
                    (run_id, target_tag),
                )
                tuples = [
                    (
                        run_id,
                        target_tag,
                        i,
                        str(r.get("root_cause", "")),
                        r.get("root_cause_score"),
                        str(r.get("propagation_path", "")),
                    )
                    for i, r in enumerate(rows)
                ]
                _executemany_chunked(cur, insert_sql, tuples, chunk_size=500)
    except Exception as e:
        logger.exception("persist_anomaly_roots: %s", e)


def persist_outlier_run(
    *,
    result_session_uuid: str,
    timeseries_dataset_id: Optional[int],
    tag_summaries: List[Dict[str, Any]],
    details_by_tag: Dict[str, Any],
    monthly_pages_by_tag: Dict[str, List[Dict[str, Any]]],
) -> Optional[int]:
    if not is_configured():
        return None
    month_sql = """
        INSERT INTO outlier_monthly_page (run_id, tag_name, month_label, page_rows)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE page_rows = VALUES(page_rows)
    """
    try:
        apply_schema_if_needed()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO outlier_run
                        (result_session_uuid, timeseries_dataset_id, tag_summaries, details_by_tag, status)
                    VALUES (%s, %s, %s, %s, 'completed')
                    """,
                    (
                        result_session_uuid,
                        timeseries_dataset_id,
                        _j(tag_summaries),
                        _j(details_by_tag),
                    ),
                )
                run_id = int(cur.lastrowid)

                month_rows: List[tuple] = []
                for tag, pages in (monthly_pages_by_tag or {}).items():
                    for p in pages:
                        month_rows.append(
                            (
                                run_id,
                                str(tag),
                                str(p.get("month", "")),
                                _j(p.get("rows", [])),
                            )
                        )
                if month_rows:
                    _executemany_chunked(cur, month_sql, month_rows, chunk_size=500)
        return int(run_id)
    except Exception as e:
        logger.exception("persist_outlier_run: %s", e)
        return None


def delete_scheduled_jobs_from_hour_bucket_onwards(hour_bucket: datetime) -> int:
    """Remove scheduled rows with hour_bucket >= that UTC day start (cascades drift/root)."""
    if not is_configured():
        return 0
    day = hour_bucket.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        apply_schema_if_needed()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM scheduled_anomaly_job WHERE hour_bucket >= %s",
                    (day,),
                )
                deleted = int(cur.rowcount or 0)
        return deleted
    except Exception as e:
        logger.exception("delete_scheduled_jobs_from_hour_bucket_onwards: %s", e)
        return 0


def scheduled_try_start_job(
    hour_bucket: datetime,
    timeseries_dataset_id: Optional[int],
    causal_dataset_id: Optional[int],
    plant_dataset_id: int,
) -> Optional[int]:
    """
    Create or reuse a row for (plant_dataset_id, hour_bucket) with status 'running'.
    Returns None if this bucket is already completed/skipped (no work).
    """
    if not is_configured():
        return None

    def _reset_to_running(cur: Any, jid: int) -> None:
        cur.execute("DELETE FROM scheduled_anomaly_drift WHERE job_id = %s", (jid,))
        cur.execute("DELETE FROM scheduled_anomaly_root WHERE job_id = %s", (jid,))
        cur.execute(
            """
            UPDATE scheduled_anomaly_job
            SET status = 'running', error_message = NULL, summary = NULL,
                finished_at = NULL, timeseries_dataset_id = %s, causal_dataset_id = %s,
                plant_dataset_id = %s
            WHERE id = %s
            """,
            (timeseries_dataset_id, causal_dataset_id, plant_dataset_id, jid),
        )

    try:
        apply_schema_if_needed()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, status FROM scheduled_anomaly_job
                    WHERE hour_bucket = %s AND plant_dataset_id = %s
                    """,
                    (hour_bucket, plant_dataset_id),
                )
                ex = cur.fetchone()
                if ex:
                    jid, st = int(ex[0]), str(ex[1])
                    if st == "completed":
                        return None
                    if st == "running":
                        cur.execute(
                            """
                            SELECT TIMESTAMPDIFF(MINUTE, created_at, NOW(6))
                            FROM scheduled_anomaly_job WHERE id = %s
                            """,
                            (jid,),
                        )
                        age = cur.fetchone()
                        age_m = int(age[0]) if age and age[0] is not None else 9999
                        if age_m < 120:
                            return None
                        _reset_to_running(cur, jid)
                        conn.commit()
                        return jid
                    _reset_to_running(cur, jid)
                    conn.commit()
                    return jid

                try:
                    cur.execute(
                        """
                        INSERT INTO scheduled_anomaly_job
                            (hour_bucket, timeseries_dataset_id, causal_dataset_id,
                             plant_dataset_id, status)
                        VALUES (%s, %s, %s, %s, 'running')
                        """,
                        (
                            hour_bucket,
                            timeseries_dataset_id,
                            causal_dataset_id,
                            plant_dataset_id,
                        ),
                    )
                    conn.commit()
                    return int(cur.lastrowid)
                except PyMySQLIntegrityError:
                    conn.rollback()
                    cur.execute(
                        """
                        SELECT id, status FROM scheduled_anomaly_job
                        WHERE hour_bucket = %s AND plant_dataset_id = %s
                        """,
                        (hour_bucket, plant_dataset_id),
                    )
                    ex2 = cur.fetchone()
                    if not ex2:
                        raise
                    jid2, st2 = int(ex2[0]), str(ex2[1])
                    if st2 == "completed":
                        return None
                    if st2 == "running":
                        cur.execute(
                            """
                            SELECT TIMESTAMPDIFF(MINUTE, created_at, NOW(6))
                            FROM scheduled_anomaly_job WHERE id = %s
                            """,
                            (jid2,),
                        )
                        age = cur.fetchone()
                        age_m = int(age[0]) if age and age[0] is not None else 9999
                        if age_m < 120:
                            return None
                        _reset_to_running(cur, jid2)
                        conn.commit()
                        return jid2
                    _reset_to_running(cur, jid2)
                    conn.commit()
                    return jid2
    except Exception as e:
        logger.exception("scheduled_try_start_job: %s", e)
        return None


def scheduled_finish_job_success(
    job_id: int,
    summary: Dict[str, Any],
    drift_rows: List[Dict[str, Any]],
) -> None:
    if not is_configured():
        return
    drift_sql = """
        INSERT INTO scheduled_anomaly_drift (job_id, rank_order, tag, drift_score)
        VALUES (%s, %s, %s, %s)
    """
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM scheduled_anomaly_drift WHERE job_id = %s", (job_id,))
                    tuples = [
                        (job_id, i, str(r.get("Tag", "")), r.get("Drift_Score"))
                        for i, r in enumerate(drift_rows)
                    ]
                    if tuples:
                        _executemany_chunked(cur, drift_sql, tuples, chunk_size=500)
                    cur.execute(
                        """
                        UPDATE scheduled_anomaly_job
                        SET status = 'completed', summary = %s, error_message = NULL,
                            finished_at = CURRENT_TIMESTAMP(6)
                        WHERE id = %s
                        """,
                        (_j(summary), job_id),
                    )
                conn.commit()
            return
        except PyMySQLOperationalError as e:
            if int(getattr(e, "args", [None])[0] or 0) in (1205, 1213) and attempt < max_attempts:
                backoff = 0.35 * attempt
                logger.warning(
                    "scheduled_finish_job_success lock conflict (attempt %s/%s, retry in %.2fs): %s",
                    attempt,
                    max_attempts,
                    backoff,
                    e,
                )
                time.sleep(backoff)
                continue
            logger.exception("scheduled_finish_job_success: %s", e)
            return
        except Exception as e:
            logger.exception("scheduled_finish_job_success: %s", e)
            return


def scheduled_replace_roots(job_id: int, roots_by_tag: Dict[str, List[Dict[str, Any]]]) -> None:
    if not is_configured():
        return
    ins_sql = """
        INSERT INTO scheduled_anomaly_root
            (job_id, target_tag, rank_order, root_cause_tag, root_cause_score, propagation_path)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM scheduled_anomaly_root WHERE job_id = %s", (job_id,))
                    tuples: List[tuple] = []
                    for tag, rows in (roots_by_tag or {}).items():
                        for i, r in enumerate(rows or []):
                            tuples.append(
                                (
                                    job_id,
                                    str(tag),
                                    i,
                                    str(r.get("root_cause", "")),
                                    r.get("root_cause_score"),
                                    str(r.get("propagation_path", "")),
                                )
                            )
                    if tuples:
                        _executemany_chunked(cur, ins_sql, tuples, chunk_size=500)
                conn.commit()
            return
        except PyMySQLOperationalError as e:
            if int(getattr(e, "args", [None])[0] or 0) in (1205, 1213) and attempt < max_attempts:
                backoff = 0.35 * attempt
                logger.warning(
                    "scheduled_replace_roots lock conflict (attempt %s/%s, retry in %.2fs): %s",
                    attempt,
                    max_attempts,
                    backoff,
                    e,
                )
                time.sleep(backoff)
                continue
            logger.exception("scheduled_replace_roots: %s", e)
            return
        except Exception as e:
            logger.exception("scheduled_replace_roots: %s", e)
            return


def scheduled_finish_job_skipped(job_id: int, message: str) -> None:
    if not is_configured():
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE scheduled_anomaly_job
                    SET status = 'skipped', error_message = %s, summary = NULL,
                        finished_at = CURRENT_TIMESTAMP(6)
                    WHERE id = %s
                    """,
                    (message[:2000], job_id),
                )
            conn.commit()
    except Exception as e:
        logger.exception("scheduled_finish_job_skipped: %s", e)


def scheduled_finish_job_failed(job_id: int, message: str) -> None:
    if not is_configured():
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE scheduled_anomaly_job
                    SET status = 'failed', error_message = %s,
                        finished_at = CURRENT_TIMESTAMP(6)
                    WHERE id = %s
                    """,
                    (message[:4000], job_id),
                )
            conn.commit()
    except Exception as e:
        logger.exception("scheduled_finish_job_failed: %s", e)
