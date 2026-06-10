"""Plant Analysis runner — same V5 pipeline as Live outlier data upload tab."""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from services.auto_without_causal_outlier_drift import _format_ts
from services.live_outlier_analysis_persist import (
    _artifacts_from_bundle,
    run_v5_bundle_from_wide_df,
)
from services.plant_analysis_multimodel_runner import _safe_float
from services.plant_analysis_results_store import STATUS_NORMAL
from services.time_series_utils import load_wide_time_series_xlsx

logger = logging.getLogger(__name__)

_ENGINE = "live_outlier"
_METHODOLOGY = "live_outlier_v5"

_FINAL_CLASS_TO_PLOT_STATUS = {
    "Normal": "normal",
    "Drift": "sudden_jump",
    "Contextual Anomaly": "mild_outlier",
    "Drift + Anomaly": "mild_outlier",
    "Strong Anomaly": "strong_outlier",
}


def _plot_status(final_class: str) -> str:
    return _FINAL_CLASS_TO_PLOT_STATUS.get(str(final_class or "").strip(), "normal")


def _peer_tags_from_x_vars(entries: Any, *, limit: int = 5) -> List[str]:
    names: List[str] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            name = str(entry.get("tag") or entry.get("feature_name") or "").strip()
        else:
            name = str(entry or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _load_wide_like_live_excel_upload(file_path: str) -> pd.DataFrame:
    """Parse wide time series using the same loader as Live outlier data upload."""
    lower = str(file_path or "").lower()
    if lower.endswith(".csv"):
        tmp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
                tmp_path = tf.name
            pd.read_csv(file_path).to_excel(tmp_path, index=False, engine="openpyxl")
            return load_wide_time_series_xlsx(tmp_path, timestamp_col_name="Timestamp")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    return load_wide_time_series_xlsx(file_path, timestamp_col_name="Timestamp")


def v5_bundle_to_points(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert V5 bundle (same shape as Live Outlier) to plant analysis result points."""
    details = result.get("details_by_tag") or {}
    limits = result.get("tag_limits_by_tag") or {}
    x_vars = result.get("x_variables_by_tag") or {}
    points: List[Dict[str, Any]] = []

    for tag, rows in details.items():
        tag_limits = limits.get(str(tag)) or {}
        lower = tag_limits.get("lo_fence") or tag_limits.get("lower")
        upper = tag_limits.get("hi_fence") or tag_limits.get("upper")
        related = _peer_tags_from_x_vars(x_vars.get(str(tag)) or [])

        for row in rows:
            final_class = str(row.get("Final_Class") or "Normal").strip()
            plot_status = _plot_status(final_class)
            is_normal = final_class in ("Normal", "", "Spike - Returned Normal")
            actual = row.get("Actual_Value")
            predicted = row.get("Predicted_Value")
            base = {
                "tag_name": str(tag),
                "observed_at": _format_ts(row.get("Timestamp")),
                "tag_value": _safe_float(actual),
                "final_class": final_class,
                "final_status": None,
                "plot_status": plot_status,
                "predicted_value": _safe_float(predicted),
                "lower_limit": _safe_float(lower),
                "upper_limit": _safe_float(upper),
            }

            if is_normal:
                points.append(
                    {
                        **base,
                        "status": STATUS_NORMAL,
                        "s5_peer_fired": None,
                        "outlier_score": None,
                        "process_issue_score": None,
                        "related_tags": [],
                        "reason": None,
                        "interpretation": None,
                        "suggested_action": None,
                        "severity": None,
                    }
                )
                continue

            reason = str(row.get("Reason") or "").strip()
            direction = str(row.get("Direction") or "").strip()
            if direction and direction != "Unknown":
                reason = f"{reason}\nDirection: {direction}." if reason else f"Direction: {direction}."

            points.append(
                {
                    **base,
                    "status": final_class,
                    "s5_peer_fired": None,
                    "outlier_score": _safe_float(
                        abs(float(actual) - float(predicted))
                        if actual is not None and predicted is not None
                        else None
                    ),
                    "process_issue_score": None,
                    "related_tags": related,
                    "reason": reason or None,
                    "interpretation": None,
                    "suggested_action": (
                        "Review strong anomalies and correlated tags for this UTC day."
                        if final_class == "Strong Anomaly"
                        else "Review drift pattern and operating context."
                    ),
                    "severity": final_class,
                }
            )

    points.sort(
        key=lambda p: (
            p.get("observed_at") or "",
            str(p.get("tag_name") or ""),
        )
    )
    return points


def run_plant_analysis_live_outlier(
    file_path: str,
    config: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Run the same V5 outlier pipeline as the main app **Live outlier data upload** tab:
    ``load_wide_time_series_xlsx`` → temp xlsx → ``run_testing_deviation_spike_v5_outlier_drift``.
    """
    del config  # Full-file V5; plant filters can be added later.

    wide = _load_wide_like_live_excel_upload(file_path)
    bundle = run_v5_bundle_from_wide_df(wide)
    # Ensure plot cache can serialize the full wide series (same as MySQL live upload artifacts).
    if bundle.get("df_for_script") is None or not isinstance(bundle.get("df_for_script"), pd.DataFrame):
        plot_wide = wide.copy()
        if "Timestamp_raw" in plot_wide.columns:
            plot_wide = plot_wide.drop(columns=["Timestamp_raw"])
        bundle["df_for_script"] = plot_wide

    points = v5_bundle_to_points(bundle)
    summary = bundle.get("summary") or {}
    artifacts = _artifacts_from_bundle(bundle)

    abnormal = sum(1 for p in points if p["status"] != STATUS_NORMAL)
    strong = sum(1 for p in points if p.get("final_class") == "Strong Anomaly")

    meta = {
        "engine": _ENGINE,
        "methodology": _METHODOLOGY,
        "total_tags": summary.get("Total_Tags") or len(bundle.get("details_by_tag") or {}),
        "total_records": summary.get("Total_Rows") or len(wide),
        "total_checks": summary.get("Total_Tag_Timestamp_Checks"),
        "actual_outlier_rows": summary.get("Actual_Outlier_Rows"),
        "warning_rows": summary.get("Warning_Rows"),
        "normal_rows": summary.get("Normal_Rows"),
        "abnormal_points": abnormal,
        "strong_anomaly_points": strong,
        "x_variables_by_tag": bundle.get("x_variables_by_tag") or {},
        "tag_limits_by_tag": bundle.get("tag_limits_by_tag") or {},
        "dataset_tags": artifacts.get("plot_tag_names") or [],
        "plot_tag_names": artifacts.get("plot_tag_names") or [],
    }
    bundle["engine"] = _ENGINE
    return bundle, points, meta
