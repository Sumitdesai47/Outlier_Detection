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
from services.plant_analysis_layman_reason import (
    build_layman_outlier_reason,
    build_simple_reason_summary,
    extract_failed_engines,
)
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


def _timestamp_parse_ratio(series: pd.Series) -> float:
    sample = series.dropna()
    if sample.empty:
        return 0.0
    if len(sample) > 500:
        sample = sample.iloc[:500]

    dt_direct = pd.to_datetime(sample, errors="coerce")
    direct_ratio = float(dt_direct.notna().mean()) if len(sample) else 0.0

    numeric = pd.to_numeric(sample, errors="coerce")
    dt_excel = pd.to_datetime(
        numeric,
        unit="D",
        origin="1899-12-30",
        errors="coerce",
    )
    excel_ratio = float(dt_excel.notna().mean()) if len(sample) else 0.0
    return max(direct_ratio, excel_ratio)


def _detect_timestamp_col(df: pd.DataFrame, config: Optional[Dict[str, Any]] = None) -> Optional[str]:
    config = config or {}
    override = str(
        config.get("timestampColumn")
        or config.get("timestamp_column")
        or ""
    ).strip()
    if override:
        if override in df.columns:
            return override
        lower_map = {str(c).strip().lower(): str(c) for c in df.columns}
        override_match = lower_map.get(override.lower())
        if override_match:
            return override_match

    best_col: Optional[str] = None
    best_score = -1.0
    hints = ("timestamp", "datetime", "date", "time", "stamp", "tiem")

    for col in df.columns:
        col_name = str(col)
        lower = col_name.strip().lower()
        parse_ratio = _timestamp_parse_ratio(df[col])
        name_bonus = 0.0
        if any(h in lower for h in hints):
            name_bonus = 0.45
        if "timestamp" in lower:
            name_bonus += 0.25
        score = parse_ratio + name_bonus
        if score > best_score:
            best_score = score
            best_col = col_name

    if best_col and _timestamp_parse_ratio(df[best_col]) >= 0.30:
        return best_col
    return None


def _apply_duration_filter(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    ts_col = _detect_timestamp_col(df, config)
    if not ts_col:
        return df
    ts = pd.to_datetime(df[ts_col], errors="coerce")
    valid = ts.notna()
    if not valid.any():
        return df

    duration = str(config.get("duration") or "full").strip().lower()
    if duration in {"full", "all", "entire", "complete"}:
        return df

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


def final_class_to_plot_status(final_class: str, *, final_status: Optional[str] = None) -> str:
    fc = str(final_class or "").strip()
    fs = str(final_status or "").strip().lower()
    if fc in ("", "Normal", "Spike - Returned Normal"):
        return "normal"
    if fc == "Strong Anomaly":
        return "strong_outlier"
    if fc == "Drift":
        return "sudden_jump"
    if fc in ("Contextual Anomaly", "Drift + Anomaly", "Anomaly"):
        return "mild_outlier"
    if "jump" in fs or "drift" in fs:
        return "sudden_jump"
    return "flagged_unclassified"


def _build_layman_reason(
    *,
    tag: str,
    row: Dict[str, Any],
    status: str,
    is_abnormal: bool,
    s5_fired: bool,
    final_class: str,
    actual: Any,
    predicted: Any,
    lower: Optional[float],
    upper: Optional[float],
    related: List[str],
    observed_at: Optional[str] = None,
) -> str:
    if not is_abnormal:
        return "No abnormal behavior detected."
    return build_layman_outlier_reason(
        tag=tag,
        row=row,
        s5_fired=s5_fired,
        final_class=final_class,
        actual=actual,
        predicted=predicted,
        lower=lower,
        upper=upper,
        related=related,
        observed_at=observed_at,
    )


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


def _peer_tag_names_from_x_vars(entries: Any, *, limit: int = 5) -> List[str]:
    """Extract display tag names from x_variables entries (dict or string)."""
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


def _classify_row(*, is_abnormal: bool, s5_fired: bool) -> str:
    """Tag issue = abnormal + S5 failed; process issue = abnormal + S5 passed."""
    if not is_abnormal:
        return STATUS_NORMAL
    if s5_fired:
        return STATUS_OUTLIER
    return STATUS_PROCESS


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
        related = _peer_tag_names_from_x_vars(x_vars.get(str(tag)) or [])

        for row in rows:
            final_class = str(row.get("Final_Class") or "Normal").strip()
            final_status = str(row.get("Final_Status") or "").strip()
            is_abnormal = final_class not in ("Normal", "", "Spike - Returned Normal")
            s5_fired = bool(
                row.get("S5_Peer_Fired")
                or "Process issue" in str(row.get("Event_Reason") or "")
                or "Process issue" in str(row.get("Anomaly_explanation") or "")
            )
            status = _classify_row(is_abnormal=is_abnormal, s5_fired=s5_fired)
            actual = row.get("Actual_Value")
            observed_at = str(row.get("Timestamp") or "")
            predicted = row.get("Predicted_Value")
            plot_status = final_class_to_plot_status(
                final_class, final_status=final_status or None
            )
            if plot_status == "normal" and s5_fired:
                plot_status = "process_issue"

            multimodel_fields = {
                "final_class": final_class,
                "final_status": final_status or None,
                "plot_status": plot_status,
                "predicted_value": _safe_float(predicted),
                "s5_peer_fired": s5_fired if (is_abnormal or s5_fired) else None,
            }

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
                        **multimodel_fields,
                    }
                )
                continue

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

            engines_fired = extract_failed_engines(row)
            reason_short = build_simple_reason_summary(
                tag=str(tag),
                final_class=final_class,
                s5_fired=s5_fired,
                row=row,
                actual=actual,
                predicted=predicted,
            )

            points.append(
                {
                    "tag_name": str(tag),
                    "observed_at": observed_at,
                    "tag_value": _safe_float(actual),
                    "status": status,
                    "outlier_score": outlier_score,
                    "process_issue_score": process_score,
                    "lower_limit": _safe_float(lower),
                    "upper_limit": _safe_float(upper),
                    "related_tags": related,
                    "reason": reason_short,
                    "reason_short": reason_short,
                    "engines_fired": engines_fired,
                    "interpretation": str(row.get("Anomaly_explanation") or row.get("Event_Reason") or "").strip(),
                    "suggested_action": (
                        "Check this instrument or control loop first — it likely does not match similar tags."
                        if s5_fired
                        else "Review plant operating conditions and related tags together — likely a process-wide change."
                    ),
                    "severity": _severity(max(outlier_score, process_score)),
                    **multimodel_fields,
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
    rolling_cfg = config.get("rolling", False)
    rolling_enabled = rolling_cfg if isinstance(rolling_cfg, bool) else str(rolling_cfg).strip().lower() == "true"
    if rolling_enabled:
        from services.plant_analysis_rolling_runner import run_plant_analysis_rolling_multimodel

        return run_plant_analysis_rolling_multimodel(file_path, config)

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
    details = result.get("details_by_tag") or {}
    meta = {
        "engine": "multimodel_outlier",
        "peer_selection_mode": result.get("peer_selection_mode"),
        "x_variables_by_tag": result.get("x_variables_by_tag") or {},
        "dataset_tags": sorted(str(t) for t in details.keys()),
        "total_tags": summary.get("Total_Tags"),
        "total_records": summary.get("Total_Rows"),
        "total_checks": summary.get("Total_Tag_Timestamp_Checks"),
        "actual_outlier_rows": summary.get("Actual_Outlier_Rows"),
        "warning_rows": summary.get("Warning_Rows"),
        "normal_rows": summary.get("Normal_Rows"),
    }
    return result, points, meta
