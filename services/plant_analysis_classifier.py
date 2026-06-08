"""Classify uploaded plant data into outlier / process issue / both / normal."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from services.plant_analysis_results_store import (
    STATUS_BOTH,
    STATUS_NORMAL,
    STATUS_OUTLIER,
    STATUS_PROCESS,
)


def _severity(score: float) -> str:
    if score >= 4:
        return "High"
    if score >= 2.5:
        return "Medium"
    return "Low"


def classify_dataframe(
    df: pd.DataFrame,
    *,
    timestamp_column: Optional[str],
    tag_columns: List[str],
    critical_tags: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    critical_tags = critical_tags or []
    if not tag_columns:
        return [], {"message": "No tag columns detected"}

    ts_col = timestamp_column
    if ts_col and ts_col in df.columns:
        df = df.copy()
        df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce")
    else:
        ts_col = None
        df = df.copy()
        df["_row_index"] = np.arange(len(df))

    numeric_df = df[tag_columns].apply(pd.to_numeric, errors="coerce")
    z_scores = (numeric_df - numeric_df.mean()) / numeric_df.std(ddof=0).replace(0, np.nan)
    z_scores = z_scores.fillna(0)

    # Process issue proxy: multiple tags deviate in same row.
    row_peer_count = (z_scores.abs() > 2.0).sum(axis=1)

    points: List[Dict[str, Any]] = []
    for tag in tag_columns:
        series = numeric_df[tag]
        z = z_scores[tag]
        mean = float(series.mean()) if series.notna().any() else 0.0
        std = float(series.std(ddof=0)) if series.notna().any() else 0.0
        lower = mean - 2.5 * std
        upper = mean + 2.5 * std

        for idx in range(len(df)):
            value = series.iloc[idx]
            if pd.isna(value):
                continue
            z_val = float(z.iloc[idx])
            is_outlier = abs(z_val) >= 2.5
            peers = int(row_peer_count.iloc[idx])
            is_process = peers >= 2 and abs(z_val) >= 1.5
            if tag in critical_tags and abs(z_val) >= 2.0:
                is_process = True

            if is_outlier and is_process:
                status = STATUS_BOTH
                reason = "Tag exceeded limits while correlated tags also deviated."
                interpretation = "Likely process upset affecting multiple sensors."
                action = "Review upstream operating conditions and related tags together."
            elif is_outlier:
                status = STATUS_OUTLIER
                reason = "Value outside expected operating band for this tag."
                interpretation = "Sensor-level deviation without broad process correlation."
                action = "Validate instrument calibration and local control loops."
            elif is_process:
                status = STATUS_PROCESS
                reason = "Multiple related tags shifted together at this timestamp."
                interpretation = "Pattern suggests a process condition change."
                action = "Check process setpoints, feed quality, and interlock history."
            else:
                status = STATUS_NORMAL
                reason = "Within expected operating range."
                interpretation = None
                action = None

            if status == STATUS_NORMAL:
                continue

            if ts_col:
                ts_val = df[ts_col].iloc[idx]
                observed_at = (
                    ts_val.isoformat() if hasattr(ts_val, "isoformat") else str(ts_val)
                )
            else:
                observed_at = str(idx)

            related = [
                other
                for other in tag_columns
                if other != tag and abs(float(z_scores[other].iloc[idx])) >= 2.0
            ]

            outlier_score = round(abs(z_val), 2)
            process_score = round(min(5.0, peers * 1.2 + (0.5 if tag in critical_tags else 0)), 2)

            points.append(
                {
                    "tag_name": tag,
                    "observed_at": observed_at,
                    "tag_value": round(float(value), 4),
                    "status": status,
                    "outlier_score": outlier_score,
                    "process_issue_score": process_score,
                    "lower_limit": round(lower, 4),
                    "upper_limit": round(upper, 4),
                    "related_tags": related[:5],
                    "reason": reason,
                    "interpretation": interpretation,
                    "suggested_action": action,
                    "severity": _severity(max(outlier_score, process_score)),
                }
            )

    summary = {
        "tags_analyzed": len(tag_columns),
        "records_processed": len(df),
    }
    return points, summary
