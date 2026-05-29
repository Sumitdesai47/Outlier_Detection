"""Business-facing view models for Multi-Signal / Dev consensus results pages."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

_STATUS_ORDER = {
    "Strong Anomaly": 0,
    "Drift + Anomaly": 1,
    "Drift": 2,
    "Warning": 3,
    "Normal": 4,
}


def _period_label(df_for_script: Any) -> str:
    if df_for_script is None or not hasattr(df_for_script, "columns"):
        return "—"
    if "Timestamp" not in df_for_script.columns:
        return "—"
    ts = pd.to_datetime(df_for_script["Timestamp"], errors="coerce")
    ts = ts[ts.notna()]
    if ts.empty:
        return "—"
    lo = ts.min().strftime("%Y-%m-%d")
    hi = ts.max().strftime("%Y-%m-%d")
    return f"{lo} to {hi}"


def _count_strong_anomaly_events(details_by_tag: Optional[Dict[str, List[Dict[str, Any]]]]) -> int:
    n = 0
    for rows in (details_by_tag or {}).values():
        for r in rows or []:
            if str(r.get("Final_Class") or "").strip() == "Strong Anomaly":
                n += 1
    return n


def _count_flagged_events(details_by_tag: Optional[Dict[str, List[Dict[str, Any]]]]) -> int:
    n = 0
    for rows in (details_by_tag or {}).values():
        for r in rows or []:
            fc = str(r.get("Final_Class") or "").strip()
            if fc not in ("", "Normal", "Spike - Returned Normal"):
                n += 1
    return n


def build_executive_summary(
    summary: Dict[str, Any],
    tag_summaries: Sequence[Dict[str, Any]],
    all_plot_tags: Sequence[str],
    *,
    df_for_script: Any = None,
    details_by_tag: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    drift_points_by_tag: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """High-level KPI cards (no internal model parameters)."""
    total_tags = len(all_plot_tags) or int(summary.get("Total_Tags") or 0)
    drift_map = drift_points_by_tag or {}
    total_drift_events = sum(int(drift_map.get(str(t)) or 0) for t in all_plot_tags)
    total_outliers = _count_flagged_events(details_by_tag)
    total_strong_outliers = _count_strong_anomaly_events(details_by_tag)
    warnings = int(summary.get("Warning_Rows") or 0)
    total_checks = int(summary.get("Total_Tag_Timestamp_Checks") or 0)
    normal_rows = int(summary.get("Normal_Rows") or 0)
    data_quality = round(100.0 * normal_rows / total_checks, 1) if total_checks else 100.0

    outlier_rate = total_outliers / total_checks if total_checks else 0.0
    if outlier_rate >= 0.08 or total_outliers >= max(30, total_checks * 0.05):
        health_status = "Critical"
        health_class = "critical"
    elif outlier_rate >= 0.02 or total_outliers > 0 or warnings > 0 or total_drift_events > 0:
        health_status = "Warning"
        health_class = "warning"
    else:
        health_status = "Healthy"
        health_class = "healthy"

    return {
        "total_tags_analyzed": total_tags,
        "total_drift_events": total_drift_events,
        "total_outliers": total_outliers,
        "total_strong_outliers": total_strong_outliers,
        "total_warnings": warnings,
        "analysis_period": _period_label(df_for_script),
        "data_quality_score": data_quality,
        "health_status": health_status,
        "health_class": health_class,
    }


def build_tag_analysis_rows(
    all_plot_tags: Sequence[str],
    tag_summaries: Sequence[Dict[str, Any]],
    details_by_tag: Dict[str, List[Dict[str, Any]]],
    x_variables_by_tag: Dict[str, List[Dict[str, Any]]],
    drift_points_by_tag: Optional[Dict[str, int]] = None,
    sudden_jumps_by_tag: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """Searchable tag table rows for Tag Analysis tab."""
    by_tag = {str(t.get("tag")): t for t in tag_summaries if t.get("tag")}
    jump_map = sudden_jumps_by_tag or {}
    rows: List[Dict[str, Any]] = []

    for tag in all_plot_tags:
        tag = str(tag)
        meta = by_tag.get(tag, {})
        detail_rows = details_by_tag.get(tag) or []
        anomaly_count = sum(
            1
            for r in detail_rows
            if str(r.get("Final_Class") or "").strip()
            not in ("", "Normal", "Spike - Returned Normal")
        )
        status = str(meta.get("status") or ("Normal" if anomaly_count == 0 else "Warning"))
        drift_score = int((drift_points_by_tag or {}).get(tag) or jump_map.get(tag) or 0)
        peers = x_variables_by_tag.get(tag) or []
        peer_preview = ", ".join(
            (
                f"{p.get('tag')} (G{p.get('group_id')})"
                if p.get("group_id") is not None
                else str(p.get("tag") or "")
            )
            for p in peers[:4]
            if p.get("tag")
        )

        rows.append(
            {
                "tag": tag,
                "status": status,
                "drift_score": drift_score,
                "anomaly_count": anomaly_count,
                "correlated_tags": peer_preview,
                "status_class": _status_class(status),
            }
        )

    rows.sort(
        key=lambda r: (
            _STATUS_ORDER.get(str(r.get("status")), 99),
            -int(r.get("anomaly_count") or 0),
            str(r.get("tag")),
        )
    )
    return rows


def _display_model_feature_name(target_tag: str, feature: str) -> str:
    tag = str(target_tag).strip()
    f = str(feature).strip()
    if f.startswith(f"{tag}__"):
        return f
    if f.startswith("peer_delta__"):
        return f"{tag}__{f}"
    return f"{tag}__{f}"


def _count_flagged_points_for_tag(details_by_tag: Dict[str, List[Dict[str, Any]]], tag: str) -> int:
    rows = details_by_tag.get(tag) or []
    return sum(
        1
        for r in rows
        if str(r.get("Final_Class") or "").strip()
        not in ("", "Normal", "Spike - Returned Normal")
    )


def build_model_summary_by_tag(
    all_plot_tags: Sequence[str],
    multimodel_meta_by_tag: Dict[str, Dict[str, Any]],
    details_by_tag: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Per-tag S5 model summary for the Model Details tab (part16 multimodel runs)."""
    rows: List[Dict[str, Any]] = []
    for tag in all_plot_tags:
        tag = str(tag)
        mm = multimodel_meta_by_tag.get(tag) or {}
        flagged = _count_flagged_points_for_tag(details_by_tag, tag)

        if mm.get("error"):
            status = "Error"
            status_class = "warning"
        elif mm.get("winner_model"):
            status = "OK"
            status_class = "healthy"
        else:
            status = "—"
            status_class = "healthy"

        features_final = list(mm.get("features_final") or [])
        n_feat = int(mm.get("n_features_in_model") or len(features_final) or 0)

        feat_labels: List[str] = []
        fs = mm.get("feature_selection") or []
        in_model = [r for r in fs if r.get("in_model")]
        if in_model:
            feat_labels = [str(r.get("feature") or "") for r in in_model if r.get("feature")]
        elif features_final:
            feat_labels = [_display_model_feature_name(tag, f) for f in features_final]

        rows.append(
            {
                "tag": tag,
                "status": status,
                "status_class": status_class,
                "model_type": str(mm.get("model_type") or "—"),
                "winner_model": str(mm.get("winner_model") or "—"),
                "features_kept": n_feat,
                "model_features": ", ".join(feat_labels),
                "flagged_points": flagged,
                "error": mm.get("error"),
                "winner_cv_rmse": mm.get("winner_cv_rmse"),
                "winner_cv_r2": mm.get("winner_cv_r2"),
            }
        )

    rows.sort(
        key=lambda r: (
            0 if r.get("status") == "OK" else 1,
            -int(r.get("flagged_points") or 0),
            str(r.get("tag") or ""),
        )
    )
    return rows


def _status_class(status: str) -> str:
    s = str(status or "").lower()
    if "strong" in s or "anomaly" in s and "drift" not in s:
        return "critical"
    if "drift" in s or "warning" in s:
        return "warning"
    return "healthy"


def build_tag_insights(
    tag: str,
    details_by_tag: Dict[str, List[Dict[str, Any]]],
    x_variables_by_tag: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Plain-language root-cause style summary for one tag."""
    rows = details_by_tag.get(tag) or []
    flagged = [
        r
        for r in rows
        if str(r.get("Final_Class") or "").strip() not in ("", "Normal")
    ]
    explanations = [
        str(r.get("Anomaly_explanation") or r.get("Reason") or "").strip()
        for r in flagged
        if str(r.get("Anomaly_explanation") or r.get("Reason") or "").strip()
    ]
    unique_explanations = list(dict.fromkeys(explanations))[:5]

    peers = x_variables_by_tag.get(tag) or []
    peer_lines = [
        f"{p.get('tag')} (correlation {float(p.get('corr') or 0):.2f})"
        for p in peers[:5]
        if p.get("tag") is not None
    ]

    if not flagged:
        summary = f"{tag} is within normal operating range for this analysis period."
    elif len(flagged) == 1:
        summary = (
            f"{tag} shows one unusual event. Review the trend chart and correlated tags "
            "to confirm whether this is an isolated spike or part of a wider process shift."
        )
    else:
        summary = (
            f"{tag} shows {len(flagged)} flagged points. Multiple signal checks agree on "
            "abnormal behavior relative to historical patterns and peer tags."
        )

    patterns: List[str] = []
    classes = {str(r.get("Final_Class") or "") for r in flagged}
    if "Strong Anomaly" in classes:
        patterns.append("Strong isolated excursions detected.")
    if "Drift" in classes or "Drift + Anomaly" in classes:
        patterns.append("Sustained level shift or drift pattern detected.")
    if not patterns:
        patterns.append("Minor deviations detected; validate against plant context.")

    return {
        "tag": tag,
        "summary": summary,
        "flagged_count": len(flagged),
        "explanations": unique_explanations,
        "contributing_tags": peer_lines,
        "patterns": patterns,
    }
