"""Persist simple Live Outlier Excel uploads (dataset name + wide time-series .xlsx)."""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict, List, Tuple

import pandas as pd

from .dataset_upload_parse import validate_excel_filename
from .db_config import get_connection, is_configured
from . import db_repository as db_repo
from .live_outlier_analysis_persist import run_v5_analysis_and_persist_for_dataset
from .time_series_utils import load_wide_time_series_xlsx

logger = logging.getLogger(__name__)

_CHUNK = 2000


def _observation_tuples_from_wide(df: pd.DataFrame, dataset_id: int) -> List[Tuple]:
    tag_cols = [c for c in df.columns if c not in ("Timestamp", "Timestamp_raw")]
    observations: List[Tuple] = []
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
            num_v = pd.to_numeric(val, errors="coerce")
            num = None if pd.isna(num_v) else float(num_v)
            observations.append((dataset_id, row_idx, ts_out, raw_ts_s, str(tag), num))
    return observations


def insert_live_outlier_excel_upload(
    dataset_name: str,
    xlsx_bytes: bytes,
    original_filename: str,
) -> Dict[str, Any]:
    """
    Parse wide time-series .xlsx (same rules as legacy timeseries upload) and store in
    ``live_outlier_excel_dataset`` + ``live_outlier_excel_observation``.
    """
    if not is_configured():
        return {
            "success": False,
            "message": "Database is not configured (set DATABASE_URL).",
            "dataset_id": None,
            "error_code": "no_database",
        }

    dataset_name = (dataset_name or "").strip()
    if not dataset_name:
        return {
            "success": False,
            "message": "Dataset name is required.",
            "dataset_id": None,
            "error_code": "validation",
        }

    try:
        validate_excel_filename(original_filename)
    except ValueError as e:
        return {
            "success": False,
            "message": str(e),
            "dataset_id": None,
            "error_code": "validation",
        }

    if not xlsx_bytes:
        return {
            "success": False,
            "message": "Excel file is empty.",
            "dataset_id": None,
            "error_code": "validation",
        }

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tf:
            tf.write(xlsx_bytes)
            tmp_path = tf.name
        df = load_wide_time_series_xlsx(tmp_path, timestamp_col_name="Timestamp")
    except ValueError as e:
        return {
            "success": False,
            "message": str(e),
            "dataset_id": None,
            "error_code": "validation",
        }
    except Exception as e:
        logger.exception("live outlier excel parse: %s", e)
        return {
            "success": False,
            "message": f"Could not read Excel: {e}",
            "dataset_id": None,
            "error_code": "validation",
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    if df.empty:
        return {
            "success": False,
            "message": "Time series sheet has no rows.",
            "dataset_id": None,
            "error_code": "validation",
        }

    tag_cols = [c for c in df.columns if c not in ("Timestamp", "Timestamp_raw")]
    if not tag_cols:
        return {
            "success": False,
            "message": "No tag columns found after the timestamp column.",
            "dataset_id": None,
            "error_code": "validation",
        }

    obs_sql = """
        INSERT INTO live_outlier_excel_observation
            (dataset_id, row_index, observed_at, observed_at_raw, tag_name, value)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            observed_at = VALUES(observed_at),
            observed_at_raw = VALUES(observed_at_raw),
            value = VALUES(value)
    """

    try:
        db_repo.apply_schema_if_needed()
    except Exception as e:
        logger.exception("schema apply (live outlier excel): %s", e)
        return {
            "success": False,
            "message": f"Database schema error: {e}",
            "dataset_id": None,
            "error_code": "db",
        }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO live_outlier_excel_dataset (dataset_name, original_filename)
                    VALUES (%s, %s)
                    """,
                    (
                        dataset_name,
                        (original_filename or "upload.xlsx").strip() or "upload.xlsx",
                    ),
                )
                dataset_id = int(cur.lastrowid)
                tuples = _observation_tuples_from_wide(df, dataset_id)
                for i in range(0, len(tuples), _CHUNK):
                    chunk = tuples[i : i + _CHUNK]
                    cur.executemany(obs_sql, chunk)
        ok_an, msg_an = run_v5_analysis_and_persist_for_dataset(int(dataset_id), df)
        base = f"Saved dataset “{dataset_name}” ({len(df)} rows, {len(tag_cols)} tags)."
        return {
            "success": True,
            "message": f"{base} {msg_an}",
            "dataset_id": dataset_id,
            "error_code": None if ok_an else "analysis_partial",
        }
    except Exception as e:
        logger.exception("insert_live_outlier_excel_upload: %s", e)
        return {
            "success": False,
            "message": f"Could not save to database: {e}",
            "dataset_id": None,
            "error_code": "db",
        }
