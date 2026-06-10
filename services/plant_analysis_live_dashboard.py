"""Live Outlier-style day dashboard backed by Plant Analysis SQLite results."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from services.plotly_json_utils import plotly_figure_to_client_json
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from services.drift_detection_service import build_plot_figure_for_tag
from services.plant_analysis_live_cache import (
    build_out_df_for_tag,
    ensure_live_cache,
    filter_wide_through_day,
    get_cached_day_drifts,
    get_cached_roots,
    get_cached_wide_df,
    observation_days_for_run,
)
from services.plant_analysis_results_store import (
    coerce_observation_day,
    get_run,
    normalize_observation_days,
    query_cached_day_drifts,
    query_has_abnormal_on_day,
    query_strong_anomaly_drifts_for_day,
    query_tag_marker_rows,
)

_TS_COL = "Timestamp"


def _day_bounds(day: date) -> Tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


def _parse_day(raw: Optional[str]) -> Optional[date]:
    iso = coerce_observation_day(raw)
    if not iso:
        return None
    return date.fromisoformat(iso)


def get_observation_days(run_id: str) -> List[str]:
    run = get_run(run_id)
    summary = (run or {}).get("summary") or {}
    return observation_days_for_run(run_id, summary)


def build_live_dashboard_overview(
    run_id: str,
    *,
    day: Optional[str] = None,
) -> Dict[str, Any]:
    run = get_run(run_id)
    if not run:
        return {"error": "Run not found"}

    summary = run.get("summary") or {}
    if summary.get("engine") != "live_outlier":
        return {
            "error": "This run was not analyzed with the Live Outlier (V5) engine. Re-upload using Live Excel upload.",
            "engine": summary.get("engine") or "multimodel",
        }

    ensure_live_cache(run_id)
    days = normalize_observation_days(get_observation_days(run_id))
    if not days:
        return {
            "run_id": run_id,
            "plant_name": run["plant_name"],
            "subsystem": run["subsystem"],
            "dataset_name": run["dataset_name"],
            "engine": "live_outlier",
            "observation_days": [],
            "selected_day": None,
            "drifts": [],
            "has_outlier_day": False,
            "has_detail_rows_for_day": False,
        }

    selected = _parse_day(day) or date.fromisoformat(days[-1])
    drifts = get_cached_day_drifts(run_id, selected)
    if drifts is None:
        drifts = query_cached_day_drifts(run_id, selected)
    if not drifts:
        drifts = query_strong_anomaly_drifts_for_day(run_id, selected)

    return {
        "run_id": run_id,
        "plant_name": run["plant_name"],
        "subsystem": run["subsystem"],
        "dataset_name": run["dataset_name"],
        "engine": "live_outlier",
        "observation_days": days,
        "selected_day": selected.isoformat(),
        "observation_first": days[0],
        "observation_last": days[-1],
        "drifts": drifts,
        "has_outlier_day": len(drifts) > 0,
        "has_detail_rows_for_day": query_has_abnormal_on_day(run_id, selected),
        "summary": summary,
    }


def build_live_tag_detail(
    run_id: str,
    *,
    day: str,
    tag: str,
    compare_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise ValueError("Run not found.")

    summary = run.get("summary") or {}
    if summary.get("engine") != "live_outlier":
        raise ValueError("Run is not a Live Outlier (V5) analysis.")

    tag = str(tag or "").strip()
    if not tag:
        raise ValueError("Missing tag.")

    selected_day = _parse_day(day)
    if not selected_day:
        raise ValueError("Invalid day; expected YYYY-MM-DD.")

    ensure_live_cache(run_id)
    wide = get_cached_wide_df(run_id)
    if wide is None or wide.empty or tag not in wide.columns:
        raise ValueError(f"Tag {tag} not available in stored series.")

    drifts = get_cached_day_drifts(run_id, selected_day)
    if drifts is None:
        drifts = query_cached_day_drifts(run_id, selected_day)
    if not drifts:
        drifts = query_strong_anomaly_drifts_for_day(run_id, selected_day)

    allowed = {str(row["tag"]) for row in drifts}
    if allowed and tag not in allowed:
        raise ValueError("This tag is not in stored strong-anomaly results for the selected UTC day.")

    drift_score = next((d["drift_score"] for d in drifts if d["tag"] == tag), None)

    marker_rows = query_tag_marker_rows(run_id, tag, through_day=selected_day)
    out_df = build_out_df_for_tag(marker_rows, tag)
    plot_df = filter_wide_through_day(wide, selected_day)

    ct = [str(x).strip() for x in (compare_tags or []) if str(x).strip()]
    fig = build_plot_figure_for_tag(plot_df, out_df, tag, compare_tags=ct)
    roots = get_cached_roots(run_id, tag)

    return {
        "tag": tag,
        "drift_score": drift_score,
        "roots": roots,
        "roots_error": None,
        "plot": plotly_figure_to_client_json(fig),
    }
