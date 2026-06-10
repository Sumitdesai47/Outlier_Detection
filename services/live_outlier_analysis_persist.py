"""Run V5 outlier pipeline (same as Outlier detection tab) and persist results for Live Outlier uploads."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .auto_without_causal_outlier_drift import run_testing_deviation_spike_v5_outlier_drift
from .db_config import get_connection

logger = logging.getLogger(__name__)

_CHUNK = 2000
_TS_COL = "Timestamp"


def _artifacts_from_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    plot_tags: List[str] = []
    df = bundle.get("df_for_script")
    if df is not None and hasattr(df, "columns"):
        plot_tags = sorted(str(c) for c in df.columns if str(c) != _TS_COL)
    return {
        "tag_limits_by_tag": bundle.get("tag_limits_by_tag") or {},
        "x_variables_by_tag": bundle.get("x_variables_by_tag") or {},
        "timestamp_summary_rows": bundle.get("timestamp_summary_rows") or [],
        "plot_tag_names": plot_tags,
    }


def _parse_ts_cell(v: Any) -> Optional[Any]:
    if v is None:
        return None
    ts = pd.to_datetime(v, errors="coerce")
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.to_pydatetime()


def persist_v5_bundle_to_db(dataset_id: int, bundle: Dict[str, Any]) -> int:
    """Insert completed run + tag summaries + detail rows. Returns run_id."""
    summary = bundle.get("summary") or {}
    artifacts = _artifacts_from_bundle(bundle)
    tag_rows_in = bundle.get("tag_summaries") or []
    details_by_tag = bundle.get("details_by_tag") or {}

    detail_sql = """
        INSERT INTO live_outlier_analysis_detail
        (run_id, tag_name, observed_at, actual_value, predicted_value, final_class, direction, reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    tag_sql = """
        INSERT INTO live_outlier_analysis_tag_summary
        (run_id, tag_name, status, first_drift_at, num_abnormal_rows)
        VALUES (%s, %s, %s, %s, %s)
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO live_outlier_analysis_run
                (dataset_id, status, summary_json, artifacts_json, started_at, finished_at)
                VALUES (%s, 'running', CAST(%s AS JSON), CAST(%s AS JSON), CURRENT_TIMESTAMP(6), NULL)
                """,
                (
                    int(dataset_id),
                    json.dumps(summary, default=str),
                    json.dumps(artifacts, default=str),
                ),
            )
            run_id = int(cur.lastrowid)

            for tr in tag_rows_in:
                tag = str(tr.get("tag") or "")
                if not tag:
                    continue
                first_at = _parse_ts_cell(tr.get("drift_timestamp"))
                cur.execute(
                    tag_sql,
                    (
                        run_id,
                        tag,
                        str(tr.get("status") or "") or None,
                        first_at,
                        int(tr.get("num_drift_points") or 0),
                    ),
                )

            tuples: List[tuple] = []
            for tag, rows in details_by_tag.items():
                for r in rows or []:
                    obs_at = _parse_ts_cell(r.get("Timestamp"))
                    tuples.append(
                        (
                            run_id,
                            str(tag),
                            obs_at,
                            r.get("Actual_Value"),
                            r.get("Predicted_Value"),
                            str(r.get("Final_Class") or "") or None,
                            str(r.get("Direction") or "") or None,
                            str(r.get("Reason") or "") or None,
                        )
                    )
            for i in range(0, len(tuples), _CHUNK):
                chunk = tuples[i : i + _CHUNK]
                cur.executemany(detail_sql, chunk)

            cur.execute(
                """
                UPDATE live_outlier_analysis_run
                SET status = 'completed', finished_at = CURRENT_TIMESTAMP(6)
                WHERE id = %s
                """,
                (run_id,),
            )
        conn.commit()
    return run_id


def insert_failed_analysis_run(dataset_id: int, message: str) -> None:
    msg = (message or "")[:65000]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO live_outlier_analysis_run
                (dataset_id, status, error_message, summary_json, artifacts_json, started_at, finished_at)
                VALUES (%s, 'failed', %s, NULL, NULL, CURRENT_TIMESTAMP(6), CURRENT_TIMESTAMP(6))
                """,
                (int(dataset_id), msg),
            )
        conn.commit()


def run_v5_bundle_from_wide_df(wide_df: pd.DataFrame) -> Dict[str, Any]:
    """Run V5 on a wide dataframe (same temp-xlsx path as Live outlier data upload)."""
    if wide_df.empty or _TS_COL not in wide_df.columns:
        raise ValueError("No Timestamp column or empty data after parse.")

    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
            tmp_path = tf.name
        export = wide_df.copy()
        if "Timestamp_raw" in export.columns:
            export = export.drop(columns=["Timestamp_raw"])
        export.to_excel(tmp_path, index=False, engine="openpyxl")
        return run_testing_deviation_spike_v5_outlier_drift(tmp_path)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def run_v5_analysis_and_persist_for_dataset(dataset_id: int, wide_df: pd.DataFrame) -> Tuple[bool, str]:
    """
    Run V5 outlier pipeline (same as Outlier detection tab) and persist results for Live Outlier uploads.
    Returns (success, user_message).
    """
    if wide_df.empty or _TS_COL not in wide_df.columns:
        insert_failed_analysis_run(dataset_id, "No Timestamp column or empty data after parse.")
        return False, "Saved upload, but outlier analysis was skipped (no usable timestamp column)."

    try:
        bundle = run_v5_bundle_from_wide_df(wide_df)
        run_id = persist_v5_bundle_to_db(int(dataset_id), bundle)
        return True, f"Saved and analyzed (run id {run_id}). Open Live Outlier detection to view stored results."
    except Exception as e:
        logger.exception("live outlier V5 persist dataset_id=%s: %s", dataset_id, e)
        try:
            insert_failed_analysis_run(dataset_id, str(e))
        except Exception as e2:
            logger.exception("insert_failed_analysis_run: %s", e2)
        return False, f"Saved upload, but outlier analysis failed: {e}"
