"""Run Plant Analysis uploads through the Multimodel Outlier Detection pipeline."""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from services.dev_outlier_detection_tab import Part15AdvancedOptions, parse_part15_advanced_json
from services.multimodel_outlier_tab import run_multimodel_outlier_tab_pipeline
from services.plant_analysis_results_store import (
    STATUS_BOTH,
    STATUS_NORMAL,
    STATUS_OUTLIER,
    STATUS_PROCESS,
)

logger = logging.getLogger(__name__)

DEFAULT_REF_Z = 3.75
DEFAULT_ENGINES = [
    "S1_GLOBAL",
    "S2_LOCAL",
    "S3_TUKEY",
    "S4_DIFF",
    "S5_PEER",
    "S6_LONG",
    "S7_TREND",
    "S8_EARLY",
]

_OP_MAP = {
    ">": ">",
    "<": "<",
    "=": "==",
    "==": "==",
    ">=": ">=",
    "<=": "<=",
    "!=": "!=",
}


def _severity(score: float) -> str:
    if score >= 4:
        return "High"
    if score >= 2.5:
        return "Medium"
    return "Low"


def build_advanced_json_from_plant_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Map Plant Analysis UI config to part16/part15 advanced JSON."""
    plant_row_filters: List[Dict[str, Any]] = []

    for cond in config.get("tagConditions") or []:
        if not isinstance(cond, dict):
            continue
        tag = str(cond.get("tag") or "").strip()
        op = _OP_MAP.get(str(cond.get("operator") or "").strip(), "")
        if not tag or not op:
            continue
        if op == "between":
            try:
                lo = float(cond.get("value"))
                hi = float(cond.get("valueTo"))
            except (TypeError, ValueError):
                continue
            plant_row_filters.append({"status_tag": tag, "operator": ">=", "value": lo})
            plant_row_filters.append({"status_tag": tag, "operator": "<=", "value": hi})
        else:
            try:
                val = float(cond.get("value"))
            except (TypeError, ValueError):
                continue
            plant_row_filters.append({"status_tag": tag, "operator": op, "value": val})

    for rule in config.get("minMaxFilters") or []:
        if not isinstance(rule, dict):
            continue
        tag = str(rule.get("tag") or "").strip()
        if not tag:
            continue
        if str(rule.get("min") or "").strip() != "":
            try:
                plant_row_filters.append(
                    {"status_tag": tag, "operator": ">=", "value": float(rule["min"])}
                )
            except (TypeError, ValueError):
                pass
        if str(rule.get("max") or "").strip() != "":
            try:
                plant_row_filters.append(
                    {"status_tag": tag, "operator": "<=", "value": float(rule["max"])}
                )
            except (TypeError, ValueError):
                pass

    direction = str(config.get("direction") or "both").strip().lower()
    critical_tags = [
        str(t).strip()
        for t in (config.get("critical_tags") or config.get("criticalTags") or [])
        if str(t).strip()
    ]

    tag_config: Dict[str, Dict[str, Any]] = {}
    for tag in critical_tags:
        tag_config[tag] = {
            "threshold": DEFAULT_REF_Z,
            "selected_engines": list(DEFAULT_ENGINES),
            "direction": direction,
        }

    return {"plant_row_filters": plant_row_filters, "tag_config": tag_config}


def _detect_timestamp_col(df: pd.DataFrame) -> Optional[str]:
    for col in df.columns:
        lower = str(col).lower()
        if any(h in lower for h in ("timestamp", "time", "date", "datetime")):
            return str(col)
    return None


def _apply_duration_filter(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    ts_col = _detect_timestamp_col(df)
    if not ts_col:
        return df
    ts = pd.to_datetime(df[ts_col], errors="coerce")
    valid = ts.notna()
    if not valid.any():
        return df

    duration = str(config.get("duration") or "6m").strip().lower()
    end = ts[valid].max()
    start: Optional[pd.Timestamp] = None

    if duration == "custom":
        raw_start = config.get("customStartDate") or config.get("custom_start_date")
        raw_end = config.get("customEndDate") or config.get("custom_end_date")
        if raw_start:
            start = pd.to_datetime(raw_start, errors="coerce")
        if raw_end:
            end = pd.to_datetime(raw_end, errors="coerce")
    elif duration == "3m":
        start = end - pd.DateOffset(months=3)
    elif duration == "1y":
        start = end - pd.DateOffset(years=1)
    else:
        start = end - pd.DateOffset(months=6)

    if start is None or pd.isna(start):
        return df
    mask = valid & (ts >= start) & (ts <= end)
    return df.loc[mask].reset_index(drop=True)


def save_upload_for_multimodel(file_storage) -> str:
    """Persist upload to a temp .xlsx path for the multimodel pipeline."""
    filename = (getattr(file_storage, "filename", None) or "upload.xlsx").lower()
    raw = file_storage.read()
    tmpdir = tempfile.mkdtemp(prefix="plant_analysis_mm_")
    if filename.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(raw))
        path = os.path.join(tmpdir, "upload.xlsx")
        df.to_excel(path, index=False)
        return path
    if filename.endswith(".xls"):
        df = pd.read_excel(io.BytesIO(raw))
        path = os.path.join(tmpdir, "upload.xlsx")
        df.to_excel(path, index=False)
        return path
    path = os.path.join(tmpdir, Path(filename).name or "upload.xlsx")
    with open(path, "wb") as fh:
        fh.write(raw)
    return path


def _classify_row(*, is_abnormal: bool, s5_fired: bool) -> Optional[str]:
    if is_abnormal and s5_fired:
        return STATUS_BOTH
    if is_abnormal:
        return STATUS_OUTLIER
    if s5_fired:
        return STATUS_PROCESS
    return STATUS_NORMAL


def workflow_result_to_points(
    result: Dict[str, Any],
    *,
    x_variables_by_tag: Optional[Dict[str, List[str]]] = None,
    tag_limits_by_tag: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[Dict[str, Any]]:
    """Convert multimodel workflow output to Plant Analysis result points."""
    details = result.get("details_by_tag") or {}
    x_vars = x_variables_by_tag or result.get("x_variables_by_tag") or {}
    limits = tag_limits_by_tag or result.get("tag_limits_by_tag") or {}
    points: List[Dict[str, Any]] = []

    for tag, rows in details.items():
        tag_limits = limits.get(str(tag)) or {}
        lower = tag_limits.get("lo_fence") or tag_limits.get("lower")
        upper = tag_limits.get("hi_fence") or tag_limits.get("upper")
        related = [str(x) for x in (x_vars.get(str(tag)) or [])][:5]

        for row in rows:
            final_class = str(row.get("Final_Class") or "Normal").strip()
            is_abnormal = final_class not in ("Normal", "")
            s5_fired = bool(
                row.get("S5_Peer_Fired")
                or "Process issue" in str(row.get("Event_Reason") or "")
                or "Process issue" in str(row.get("Anomaly_explanation") or "")
            )
            status = _classify_row(is_abnormal=is_abnormal, s5_fired=s5_fired)
            actual = row.get("Actual_Value")
            observed_at = str(row.get("Timestamp") or "")

            if status == STATUS_NORMAL:
                points.append(
                    {
                        "tag_name": str(tag),
                        "observed_at": observed_at,
                        "tag_value": _safe_float(actual),
                        "status": STATUS_NORMAL,
                        "outlier_score": None,
                        "process_issue_score": None,
                        "lower_limit": _safe_float(lower),
                        "upper_limit": _safe_float(upper),
                        "related_tags": [],
                        "reason": None,
                        "interpretation": None,
                        "suggested_action": None,
                        "severity": None,
                    }
                )
                continue

            predicted = row.get("Predicted_Value")
            outlier_score = 0.0
            try:
                if actual is not None and predicted is not None:
                    outlier_score = round(abs(float(actual) - float(predicted)), 2)
            except (TypeError, ValueError):
                outlier_score = 0.0

            process_score = round(4.0 if s5_fired else 1.0, 2)
            if final_class == "Strong Anomaly":
                outlier_score = max(outlier_score, 3.5)
            elif final_class == "Drift":
                process_score = max(process_score, 2.5)

            points.append(
                {
                    "tag_name": str(tag),
                    "observed_at": str(row.get("Timestamp") or ""),
                    "tag_value": _safe_float(actual),
                    "status": status,
                    "outlier_score": outlier_score,
                    "process_issue_score": process_score,
                    "lower_limit": _safe_float(lower),
                    "upper_limit": _safe_float(upper),
                    "related_tags": related,
                    "reason": str(row.get("Reason") or row.get("Anomaly_explanation") or "").strip(),
                    "interpretation": str(row.get("Anomaly_explanation") or row.get("Event_Reason") or "").strip(),
                    "suggested_action": (
                        "Review correlated tags and process conditions."
                        if status in (STATUS_BOTH, STATUS_PROCESS)
                        else "Validate sensor and local control response."
                    ),
                    "severity": _severity(max(outlier_score, process_score)),
                }
            )

    points.sort(
        key=lambda p: (
            pd.to_datetime(p.get("observed_at"), errors="coerce") or pd.Timestamp.min,
            str(p.get("tag_name") or ""),
        )
    )
    return points


def _safe_float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        f = float(val)
        return round(f, 4) if pd.notna(f) else None
    except (TypeError, ValueError):
        return None


def run_plant_analysis_multimodel(
    file_path: str,
    config: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Execute multimodel outlier detection and return (workflow_result, points, meta).
    """
    advanced_payload = build_advanced_json_from_plant_config(config)
    advanced, adv_err = parse_part15_advanced_json(json.dumps(advanced_payload))
    if adv_err:
        raise ValueError(adv_err)

    critical_tags = [
        str(t).strip()
        for t in (config.get("critical_tags") or config.get("criticalTags") or [])
        if str(t).strip()
    ]
    tag_config_used = bool(critical_tags)

    result = run_multimodel_outlier_tab_pipeline(
        file_path,
        critical_tags=critical_tags,
        tag_config_used=tag_config_used,
        advanced=advanced,
    )

    points = workflow_result_to_points(result)
    summary = result.get("summary") or {}
    meta = {
        "engine": "multimodel_outlier",
        "peer_selection_mode": result.get("peer_selection_mode"),
        "total_tags": summary.get("Total_Tags"),
        "total_records": summary.get("Total_Rows"),
        "total_checks": summary.get("Total_Tag_Timestamp_Checks"),
        "actual_outlier_rows": summary.get("Actual_Outlier_Rows"),
        "warning_rows": summary.get("Warning_Rows"),
        "normal_rows": summary.get("Normal_Rows"),
    }
    return result, points, meta
