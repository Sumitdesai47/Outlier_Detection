"""Rolling multimodel outlier detection for Plant Analysis (30-row cooling + expanding window)."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from services.auto_without_causal_outlier_drift import _load_auto_without_causal_module
from services.auto_without_causal_outlier_drift import _format_ts
from services.dev_outlier_detection_tab import Part15AdvancedOptions, parse_part15_advanced_json
from services.plant_analysis_layman_reason import build_simple_reason_summary, extract_failed_engines
from services.plant_analysis_multimodel_runner import (
    _classify_row,
    _safe_float,
    _severity,
    build_advanced_json_from_plant_config,
    final_class_to_plot_status,
)
from services.plant_analysis_results_store import STATUS_NORMAL, STATUS_OUTLIER, STATUS_PROCESS
from services.robust_consensus_outlier_workflow import (
    MULTI_SIGNAL_PRESET,
    run_multi_signal_outlier_detection,
)

logger = logging.getLogger(__name__)

COOLING_PERIOD_ROWS = 30
_TS_COL = "Timestamp"


def _normalize_ts(value: Any) -> Optional[pd.Timestamp]:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    out = pd.Timestamp(ts)
    if out.tzinfo is not None:
        out = out.tz_convert(None)
    return out.floor("s")


def _load_full_uploaded_wide(file_path: str, config: Dict[str, Any]) -> pd.DataFrame:
    """Load the complete uploaded file (all rows, best sheet) — no duration trim."""
    mod = _load_auto_without_causal_module()
    ts_override = str(
        config.get("timestampColumn") or config.get("timestamp_column") or ""
    ).strip() or None

    raw_df, selected_sheet = mod.read_input_file(
        file_path,
        sheet_name=None,
        max_rows=None,
        datetime_format=None,
    )
    if raw_df.empty:
        raise ValueError("Uploaded file is empty.")

    ts_col = mod.detect_timestamp_col(raw_df, override=ts_override, datetime_format=None)
    if not ts_col:
        raise ValueError("No timestamp column found in uploaded file.")

    tag_cols_arg = mod.parse_tag_cols_argument(None)
    long_df, _input_fmt, _dts, _dtag, _dval = mod.make_long_format(
        raw_df,
        timestamp_col=ts_col,
        tag_col=None,
        value_col=None,
        tag_cols=tag_cols_arg,
        datetime_format=None,
    )
    pivot = mod.build_pivot(long_df)
    if pivot.empty:
        raise ValueError("No tag columns after pivot; check input format.")

    wide = pivot.reset_index()
    if _TS_COL not in wide.columns:
        raise ValueError("Pivot result is missing Timestamp column.")

    wide[_TS_COL] = pd.to_datetime(wide[_TS_COL], errors="coerce")
    wide = wide.dropna(subset=[_TS_COL]).sort_values(_TS_COL).reset_index(drop=True)
    if wide.empty:
        raise ValueError("No valid timestamps in uploaded file.")

    logger.info(
        "rolling: loaded %s rows from sheet %r (%s)",
        len(wide),
        selected_sheet,
        file_path,
    )
    return wide


def _numeric_tags(wide: pd.DataFrame, *, min_rows: int) -> List[str]:
    tags: List[str] = []
    for col in wide.columns:
        if col == _TS_COL:
            continue
        series = pd.to_numeric(wide[col], errors="coerce")
        if series.notna().sum() >= max(min_rows, 10):
            wide[col] = series
            tags.append(str(col))
    return tags


def _rows_at_timestamp(bundle: Dict[str, Any], ts: pd.Timestamp) -> Dict[str, Dict[str, Any]]:
    """Per-tag detection row at ``ts`` from multimodel bundle."""
    target = _normalize_ts(ts)
    if target is None:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    details = bundle.get("details_by_tag") or {}
    for tag, rows in details.items():
        for row in rows or []:
            r_ts = _normalize_ts(row.get("Timestamp"))
            if r_ts is None or r_ts != target:
                continue
            out[str(tag)] = dict(row)
            break
    return out


def _point_from_row(
    *,
    tag: str,
    ts: pd.Timestamp,
    tag_value: Any,
    row: Optional[Dict[str, Any]],
    bundle: Dict[str, Any],
    lower: Optional[float],
    upper: Optional[float],
) -> Dict[str, Any]:
    x_vars = bundle.get("x_variables_by_tag") or {}
    related = [str(x.get("tag") if isinstance(x, dict) else x) for x in (x_vars.get(tag) or [])][:5]

    if row:
        final_class = str(row.get("Final_Class") or "Normal").strip()
        final_status = str(row.get("Final_Status") or "").strip()
        actual = row.get("Actual_Value", tag_value)
        predicted = row.get("Predicted_Value")
    else:
        final_class = "Normal"
        final_status = "Normal"
        actual = tag_value
        predicted = None

    plot_status = final_class_to_plot_status(final_class, final_status=final_status or None)
    is_abnormal = final_class not in ("Normal", "", "Spike - Returned Normal")
    s5_fired = bool(row.get("S5_Peer_Fired")) if row else False
    if plot_status == "normal" and s5_fired:
        plot_status = "process_issue"
    status = _classify_row(is_abnormal=is_abnormal, s5_fired=s5_fired)

    base = {
        "tag_name": tag,
        "observed_at": _format_ts(ts),
        "tag_value": _safe_float(actual if actual is not None else tag_value),
        "final_class": final_class,
        "final_status": final_status or None,
        "plot_status": plot_status,
        "predicted_value": _safe_float(predicted),
        "lower_limit": _safe_float(lower),
        "upper_limit": _safe_float(upper),
    }

    if status == STATUS_NORMAL:
        return {
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

    outlier_score = 0.0
    try:
        if actual is not None and predicted is not None:
            outlier_score = round(abs(float(actual) - float(predicted)), 2)
    except (TypeError, ValueError):
        outlier_score = 0.0
    process_score = round(4.0 if status == STATUS_PROCESS else 1.0, 2)
    if final_class == "Strong Anomaly":
        outlier_score = max(outlier_score, 3.5)
    elif final_class == "Drift":
        process_score = max(process_score, 2.5)

    row_payload = row or {}
    engines_fired = extract_failed_engines(row_payload)
    reason_short = build_simple_reason_summary(
        tag=tag,
        final_class=final_class,
        s5_fired=s5_fired,
        row=row_payload,
        actual=actual,
        predicted=predicted,
    )

    return {
        **base,
        "status": status,
        "s5_peer_fired": s5_fired,
        "outlier_score": outlier_score,
        "process_issue_score": process_score,
        "related_tags": related,
        "reason": reason_short,
        "reason_short": reason_short,
        "engines_fired": engines_fired,
        "interpretation": str((row or {}).get("Anomaly_explanation") or "").strip() or None,
        "suggested_action": (
            "Review correlated tags and process conditions for a plant-wide shift."
            if status == STATUS_PROCESS
            else "Validate sensor calibration and check peer tag agreement."
        ),
        "severity": _severity(max(outlier_score, process_score)),
    }


def run_plant_analysis_rolling_multimodel(
    file_path: str,
    config: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Expanding-window multimodel detection after a 30-row cooling period.

    Rows 1–30: cooling only (not analyzed).
    Row 31 (index 30): analyze using rows 1–30 as history.
    Row 32 (index 31): analyze using rows 1–31 as history.
    … continues through the last uploaded row.
    """
    advanced_payload = build_advanced_json_from_plant_config(config)
    advanced, adv_err = parse_part15_advanced_json(json.dumps(advanced_payload))
    if adv_err:
        raise ValueError(adv_err)

    wide = _load_full_uploaded_wide(file_path, config)
    total_rows = len(wide)
    if total_rows <= COOLING_PERIOD_ROWS:
        raise ValueError(
            f"Need more than {COOLING_PERIOD_ROWS} timestamps for rolling analysis "
            f"(found {total_rows})."
        )

    tag_cols = _numeric_tags(wide, min_rows=10)
    if not tag_cols:
        raise ValueError("No usable numeric tag columns after cleaning.")

    critical_tags = [
        str(t).strip()
        for t in (config.get("critical_tags") or config.get("criticalTags") or [])
        if str(t).strip()
    ]
    per_tag = advanced.per_tag_controls or {}
    if critical_tags:
        per_tag = {k: v for k, v in per_tag.items() if k in critical_tags}
        tag_cols = [t for t in tag_cols if t in critical_tags] or tag_cols

    steps_total = total_rows - COOLING_PERIOD_ROWS
    logger.info(
        "rolling: analyzing rows %s–%s (%s steps, %s tags)",
        COOLING_PERIOD_ROWS + 1,
        total_rows,
        steps_total,
        len(tag_cols),
    )

    k = float(MULTI_SIGNAL_PRESET.get("k_global_robust_z", 3.75))
    points: List[Dict[str, Any]] = []
    analyzed_timestamps = 0
    bundle: Dict[str, Any] = {}

    # Expanding window: for row index `idx`, train on rows 0..idx (inclusive).
    # Do not apply plant_row_filters here — they drop rows and break row alignment.
    for idx in range(COOLING_PERIOD_ROWS, total_rows):
        step = wide.iloc[0 : idx + 1].copy()
        history = wide.iloc[0:idx]
        ts = pd.Timestamp(wide.at[idx, _TS_COL])

        bundle = run_multi_signal_outlier_detection(
            step,
            tag_config=per_tag or None,
            plant_row_filters=None,
            plant_status_filter=None,
            config=MULTI_SIGNAL_PRESET,
            critical_tags=critical_tags or None,
        )
        current_rows = _rows_at_timestamp(bundle, ts)
        limits_map = bundle.get("tag_limits_by_tag") or {}

        for tag in tag_cols:
            val = pd.to_numeric(wide.at[idx, tag], errors="coerce")
            b = pd.to_numeric(history[tag], errors="coerce").dropna()
            mean = float(b.mean()) if not b.empty else np.nan
            std = float(b.std(ddof=0)) if len(b) > 1 else np.nan
            lower = mean - k * std if np.isfinite(mean) and np.isfinite(std) else np.nan
            upper = mean + k * std if np.isfinite(mean) and np.isfinite(std) else np.nan

            tag_limits = limits_map.get(tag) or {}
            lo = tag_limits.get("lo_fence") or tag_limits.get("lower") or lower
            hi = tag_limits.get("hi_fence") or tag_limits.get("upper") or upper

            points.append(
                _point_from_row(
                    tag=tag,
                    ts=ts,
                    tag_value=val,
                    row=current_rows.get(tag),
                    bundle=bundle,
                    lower=_safe_float(lo),
                    upper=_safe_float(hi),
                )
            )
        analyzed_timestamps += 1
        if analyzed_timestamps % 50 == 0 or analyzed_timestamps == steps_total:
            logger.info(
                "rolling multimodel progress: %s / %s rows",
                analyzed_timestamps,
                steps_total,
            )

    points.sort(
        key=lambda p: (
            pd.to_datetime(p.get("observed_at"), errors="coerce") or pd.Timestamp.min,
            str(p.get("tag_name") or ""),
        )
    )

    abnormal = sum(1 for p in points if p["status"] != STATUS_NORMAL)
    meta = {
        "engine": "multimodel_outlier",
        "x_variables_by_tag": bundle.get("x_variables_by_tag") or {},
        "dataset_tags": [str(t) for t in tag_cols],
        "methodology": "rolling_expanding",
        "cooling_period_rows": COOLING_PERIOD_ROWS,
        "analyzed_timestamps": analyzed_timestamps,
        "total_tags": len(tag_cols),
        "total_records": total_rows,
        "total_checks": len(points),
        "abnormal_points": abnormal,
        "normal_rows": len(points) - abnormal,
        "input_rows_loaded": total_rows,
        "rows_analyzed": analyzed_timestamps,
    }

    workflow = {
        "summary": {
            "Total_Tags": len(tag_cols),
            "Total_Rows": total_rows,
            "Analyzed_Rows": analyzed_timestamps,
            "Cooling_Period_Rows": COOLING_PERIOD_ROWS,
            "Methodology": "Rolling expanding window (multimodel S1–S8)",
        },
        "details_by_tag": {},
        "tag_summaries": [],
        "engine": "multimodel_outlier",
        "rolling_meta": meta,
    }
    return workflow, points, meta
