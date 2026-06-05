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


HEALTH_HEALTHY_MAX_PCT = 20.0
HEALTH_WARNING_MAX_PCT = 30.0


HEALTH_CATEGORY_DEFS: List[Dict[str, str]] = [
    {
        "name": "Healthy",
        "class": "healthy",
        "threshold": "< 20% flagged",
        "range": (
            "Less than 20% of all tag×time checks are flagged (non-normal events)."
        ),
    },
    {
        "name": "Warning",
        "class": "warning",
        "threshold": "20% to 30% flagged",
        "range": (
            "Flagged share is at least 20% and at most 30% of all tag×time checks."
        ),
    },
    {
        "name": "Critical",
        "class": "critical",
        "threshold": "> 30% flagged",
        "range": (
            "More than 30% of all tag×time checks are flagged."
        ),
    },
]


def _flagged_rate_from_summary(
    *,
    total_checks: int,
    normal_rows: int,
) -> tuple[float, float, int, float]:
    """
    Flagged % and Data Quality from the same fraction (avoids 75.0% quality vs 25.1% flagged drift).
    """
    if total_checks <= 0:
        return 0.0, 0.0, 0, 100.0
    flagged_rows = max(0, total_checks - normal_rows)
    flagged_rate_pct = round(100.0 * flagged_rows / total_checks, 2)
    outlier_rate = flagged_rate_pct / 100.0
    data_quality_score = round(100.0 - flagged_rate_pct, 1)
    return outlier_rate, flagged_rate_pct, flagged_rows, data_quality_score


def _health_status_from_flagged_pct(flagged_rate_pct: float) -> tuple[str, str]:
    pct = float(flagged_rate_pct)
    if pct > HEALTH_WARNING_MAX_PCT:
        return "Critical", "critical"
    if pct >= HEALTH_HEALTHY_MAX_PCT:
        return "Warning", "warning"
    return "Healthy", "healthy"


def health_from_data_quality_score(data_quality_score: float) -> tuple[str, str, float]:
    """Derive health band from the Data Quality KPI (flagged % = 100 − quality)."""
    flagged_rate_pct = round(100.0 - float(data_quality_score), 2)
    status, cls = _health_status_from_flagged_pct(flagged_rate_pct)
    return status, cls, flagged_rate_pct


def _health_detail_bullets(
    *,
    health_status: str,
    total_tags: int,
    flagged_rate_pct: float,
    total_outliers: int,
    total_strong_outliers: int,
    total_drift_events: int,
    total_warnings: int,
    total_checks: int,
    outlier_rate: float,
) -> List[str]:
    """Crisp bullet points for the Overall Health info panel."""
    data_quality_pct = round(100.0 - flagged_rate_pct, 1)
    bullets: List[str] = [
        (
            f"We scored {total_tags} tag(s) across {total_checks:,} timestamp checks. "
            f"Data Quality Score is {data_quality_pct:.1f}% normal "
            f"({flagged_rate_pct:.1f}% flagged — {total_outliers:,} non-normal rows"
            + (
                f", {total_strong_outliers:,} strong anomalies"
                if total_strong_outliers
                else ""
            )
            + "). Overall Health uses the same flagged % as this tile."
        ),
    ]

    if health_status == "Critical":
        bullets.append(
            f"Rated **Critical** because flagged rate is {flagged_rate_pct:.1f}% "
            f"(above the {HEALTH_WARNING_MAX_PCT:.0f}% Critical threshold)."
        )
        bullets.append(
            "This means abnormal readings are widespread or frequent enough that the full "
            "upload period should be reviewed urgently — not just a single tag."
        )
        bullets.append(
            "Next: open Event Details and Graph for the worst tags; confirm whether "
            "issues are instrument, process, or peer-related (S5)."
        )
    elif health_status == "Warning":
        bullets.append(
            f"Rated **Warning** because flagged rate is {flagged_rate_pct:.1f}% "
            f"(between {HEALTH_HEALTHY_MAX_PCT:.0f}% and {HEALTH_WARNING_MAX_PCT:.0f}% inclusive)."
        )
        bullets.append(
            "The plant period shows measurable deviation from normal but has not crossed "
            "Critical limits. Validate whether drift is real or a calibration/data-quality effect."
        )
    else:
        bullets.append(
            f"Rated **Healthy** because flagged rate is {flagged_rate_pct:.1f}% "
            f"(below the {HEALTH_HEALTHY_MAX_PCT:.0f}% Healthy threshold)."
        )
        bullets.append(
            "No material multi-signal or peer-residual concern for this period at the plant level."
        )

    return [b.replace("**", "") for b in bullets]


def _health_why_status(
    *,
    health_status: str,
    flagged_rate_pct: float,
) -> str:
    """One sentence: which rule placed this run in the current health band."""
    if health_status == "Critical":
        return (
            f"Your uploaded data is rated Critical because {flagged_rate_pct:.1f}% of checks "
            f"were flagged (more than {HEALTH_WARNING_MAX_PCT:.0f}%)."
        )
    if health_status == "Warning":
        return (
            f"Your uploaded data is rated Warning because {flagged_rate_pct:.1f}% of checks "
            f"were flagged (from {HEALTH_HEALTHY_MAX_PCT:.0f}% up to {HEALTH_WARNING_MAX_PCT:.0f}%)."
        )
    return (
        f"Your uploaded data is rated Healthy because {flagged_rate_pct:.1f}% of checks "
        f"were flagged (less than {HEALTH_HEALTHY_MAX_PCT:.0f}%)."
    )


def _health_explanation(
    *,
    health_status: str,
    flagged_rate_pct: float,
    total_outliers: int,
    total_strong_outliers: int,
    total_drift_events: int,
    total_warnings: int,
    total_checks: int,
) -> str:
    base = (
        f"Based on {total_checks:,} tag×time checks: "
        f"{flagged_rate_pct:.1f}% flagged ({total_outliers:,} events"
    )
    if total_strong_outliers:
        base += f", {total_strong_outliers:,} strong anomalies"
    base += ")."
    if health_status == "Critical":
        return (
            f"{base} Anomaly rate is high enough to treat the plant period as needing urgent review."
        )
    if health_status == "Warning":
        parts = [base, " Some deviation from normal was detected"]
        if total_drift_events:
            parts.append(f" ({total_drift_events:,} drift-related points)")
        if total_warnings:
            parts.append(f" ({total_warnings:,} warning rows)")
        parts.append("; validate in Event Details and Graph tabs.")
        return "".join(parts)
    return (
        f"{base} No material drift or flagged events for this period — operating within expected range."
    )


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
    total_strong_outliers = _count_strong_anomaly_events(details_by_tag)
    warnings = int(summary.get("Warning_Rows") or 0)
    total_checks = int(summary.get("Total_Tag_Timestamp_Checks") or 0)
    normal_rows = int(summary.get("Normal_Rows") or 0)

    outlier_rate, flagged_rate_pct, total_outliers, data_quality = _flagged_rate_from_summary(
        total_checks=total_checks,
        normal_rows=normal_rows,
    )
    health_status, health_class = _health_status_from_flagged_pct(flagged_rate_pct)

    health_why_status = _health_why_status(
        health_status=health_status,
        flagged_rate_pct=flagged_rate_pct,
    )
    health_detail_bullets = _health_detail_bullets(
        health_status=health_status,
        total_tags=total_tags,
        flagged_rate_pct=flagged_rate_pct,
        total_outliers=total_outliers,
        total_strong_outliers=total_strong_outliers,
        total_drift_events=total_drift_events,
        total_warnings=warnings,
        total_checks=total_checks,
        outlier_rate=outlier_rate,
    )
    health_explanation = _health_explanation(
        health_status=health_status,
        flagged_rate_pct=flagged_rate_pct,
        total_outliers=total_outliers,
        total_strong_outliers=total_strong_outliers,
        total_drift_events=total_drift_events,
        total_warnings=warnings,
        total_checks=total_checks,
    )

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
        "health_flagged_rate_pct": flagged_rate_pct,
        "health_why_status": health_why_status,
        "health_detail_bullets": health_detail_bullets,
        "health_explanation": health_explanation,
        "health_healthy_max_pct": HEALTH_HEALTHY_MAX_PCT,
        "health_warning_max_pct": HEALTH_WARNING_MAX_PCT,
        "health_categories": [
            {**cat, "is_current": cat["name"] == health_status}
            for cat in HEALTH_CATEGORY_DEFS
        ],
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
