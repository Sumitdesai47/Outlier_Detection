"""Precompute and persist Live Outlier dashboard cache at upload time (V5 bundle)."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, time
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from services.live_outlier_dashboard import (
    _FINAL_CLASS_TO_PLOT_STATUS,
    _STRONG_ANOMALY_CLASS,
    correlation_related_tags,
    live_outlier_related_tag_limit,
)
from services.plant_analysis_results_store import (
    delete_live_cache_for_run,
    get_live_cache,
    has_live_cache,
    normalize_observation_days,
    query_all_strong_anomaly_day_drifts,
    save_live_cache,
)

logger = logging.getLogger(__name__)

_TS_COL = "Timestamp"


def _serialize_wide(df: pd.DataFrame) -> str:
    frame = df.copy()
    if _TS_COL in frame.columns:
        frame[_TS_COL] = pd.to_datetime(frame[_TS_COL], errors="coerce").astype(str)
    return json.dumps(frame.to_dict(orient="split"), default=str)


def deserialize_wide(wide_json: str) -> pd.DataFrame:
    payload = json.loads(wide_json)
    df = pd.DataFrame(payload["data"], columns=payload["columns"])
    if _TS_COL in df.columns:
        df[_TS_COL] = pd.to_datetime(df[_TS_COL], errors="coerce")
    return df


def _day_drifts_from_bundle(bundle: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Strong-anomaly tag ranking per UTC calendar day from V5 bundle details."""
    details = bundle.get("details_by_tag") or {}
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for tag, rows in details.items():
        for row in rows or []:
            if str(row.get("Final_Class") or "").strip() != _STRONG_ANOMALY_CLASS:
                continue
            ts = pd.to_datetime(row.get("Timestamp"), errors="coerce")
            if pd.isna(ts):
                continue
            day_iso = ts.date().isoformat()
            counts[day_iso][str(tag)] += 1

    out: Dict[str, List[Dict[str, Any]]] = {}
    for day_iso, per_tag in counts.items():
        ranked = sorted(per_tag.items(), key=lambda item: (-item[1], item[0]))
        out[day_iso] = [
            {"rank": idx + 1, "tag": tag_name, "drift_score": float(score)}
            for idx, (tag_name, score) in enumerate(ranked)
        ]
    return out


def _roots_from_bundle(bundle: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Reuse V5 precomputed x-variable correlations when available."""
    x_vars = bundle.get("x_variables_by_tag") or {}
    if not x_vars:
        return {}
    limit = live_outlier_related_tag_limit()
    roots: Dict[str, List[Dict[str, Any]]] = {}
    for tag, items in x_vars.items():
        rows: List[Dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            peer = str(item.get("tag") or "").strip()
            if not peer:
                continue
            rows.append(
                {
                    "root_cause": peer,
                    "root_cause_score": float(item.get("corr") or 0.0),
                    "propagation_path": "",
                }
            )
        roots[str(tag)] = rows[:limit]
    return roots


def _roots_from_wide(wide: pd.DataFrame) -> Dict[str, List[Dict[str, Any]]]:
    limit = live_outlier_related_tag_limit()
    roots: Dict[str, List[Dict[str, Any]]] = {}
    if wide.empty or _TS_COL not in wide.columns:
        return roots
    for col in wide.columns:
        if col == _TS_COL:
            continue
        roots[str(col)] = correlation_related_tags(wide, str(col), limit=limit)
    return roots


def _wide_from_bundle(bundle: Dict[str, Any]) -> pd.DataFrame:
    df = bundle.get("df_for_script")
    if isinstance(df, pd.DataFrame) and not df.empty:
        return df.copy()
    return pd.DataFrame(columns=[_TS_COL])


def _wide_from_slim_points(points: List[Dict[str, Any]]) -> pd.DataFrame:
    by_ts: Dict[str, Dict[str, Any]] = {}
    for point in points:
        ts = pd.to_datetime(point.get("observed_at"), errors="coerce")
        if pd.isna(ts):
            continue
        key = ts.isoformat()
        row = by_ts.setdefault(key, {_TS_COL: ts})
        tag = str(point.get("tag_name") or "")
        if tag:
            row[tag] = point.get("tag_value")
    if not by_ts:
        return pd.DataFrame(columns=[_TS_COL])
    return pd.DataFrame(by_ts.values()).sort_values(_TS_COL)


def build_cache_from_bundle(run_id: str, bundle: Dict[str, Any]) -> None:
    """Persist wide series, per-tag correlation roots, and per-day drift lists."""
    wide = _wide_from_bundle(bundle)
    if wide.empty or _TS_COL not in wide.columns:
        logger.warning("live cache skipped run_id=%s: empty wide", run_id)
        return

    plot_tags = sorted(str(c) for c in wide.columns if str(c) != _TS_COL)
    roots = _roots_from_bundle(bundle) or _roots_from_wide(wide)
    day_drifts = _day_drifts_from_bundle(bundle)

    delete_live_cache_for_run(run_id)
    save_live_cache(
        run_id,
        wide_json=_serialize_wide(wide),
        roots_json=json.dumps(roots, default=str),
        plot_tags_json=json.dumps(plot_tags),
        day_drifts=day_drifts,
    )
    logger.info(
        "live cache saved run_id=%s tags=%s days=%s",
        run_id,
        len(plot_tags),
        len(day_drifts),
    )


def backfill_cache_from_db(run_id: str) -> bool:
    """One-time cache build for runs uploaded before caching existed."""
    if has_live_cache(run_id):
        return True

    from services.plant_analysis_results_store import query_slim_series_points

    from services.plant_analysis_results_store import get_run

    run = get_run(run_id)
    summary = (run or {}).get("summary") or {}

    points = query_slim_series_points(run_id)
    if not points:
        return False

    wide = _wide_from_slim_points(points)
    if wide.empty:
        return False

    roots = _roots_from_bundle({"x_variables_by_tag": summary.get("x_variables_by_tag")})
    if not roots:
        roots = _roots_from_wide(wide)
    day_drifts = query_all_strong_anomaly_day_drifts(run_id)
    plot_tags = sorted(str(c) for c in wide.columns if str(c) != _TS_COL)
    save_live_cache(
        run_id,
        wide_json=_serialize_wide(wide),
        roots_json=json.dumps(roots, default=str),
        plot_tags_json=json.dumps(plot_tags),
        day_drifts=day_drifts,
    )
    return True


def ensure_live_cache(run_id: str, bundle: Optional[Dict[str, Any]] = None) -> None:
    if has_live_cache(run_id):
        return
    if bundle is not None:
        build_cache_from_bundle(run_id, bundle)
        return
    backfill_cache_from_db(run_id)


def get_cached_wide_df(run_id: str) -> Optional[pd.DataFrame]:
    row = get_live_cache(run_id)
    if not row or not row.get("wide_json"):
        return None
    return deserialize_wide(str(row["wide_json"]))


def get_cached_roots(run_id: str, tag: str) -> List[Dict[str, Any]]:
    row = get_live_cache(run_id)
    if not row or not row.get("roots_json"):
        return []
    try:
        roots_map = json.loads(row["roots_json"])
    except json.JSONDecodeError:
        return []
    if not isinstance(roots_map, dict):
        return []
    items = roots_map.get(str(tag)) or []
    return items if isinstance(items, list) else []


def get_cached_day_drifts(run_id: str, day: date) -> Optional[List[Dict[str, Any]]]:
    row = get_live_cache(run_id)
    if not row:
        return None
    drifts_map = row.get("day_drifts") or {}
    return drifts_map.get(day.isoformat())


def observation_days_for_run(run_id: str, summary: Dict[str, Any]) -> List[str]:
    row = get_live_cache(run_id)
    if row and row.get("day_drifts"):
        days = sorted(str(d) for d in row["day_drifts"].keys())
        return normalize_observation_days(days)
    cached = summary.get("observation_days")
    if isinstance(cached, list) and cached:
        return normalize_observation_days([str(d) for d in cached])
    from services.plant_analysis_results_store import query_distinct_observation_days

    return normalize_observation_days(query_distinct_observation_days(run_id))


def build_out_df_for_tag(
    marker_rows: Sequence[Dict[str, Any]],
    tag: str,
) -> pd.DataFrame:
    tag = str(tag)
    recs: List[Dict[str, Any]] = []
    for row in marker_rows:
        ts = pd.to_datetime(row.get("observed_at"), errors="coerce")
        if pd.isna(ts):
            continue
        fc = str(row.get("final_class") or "").strip()
        status = _FINAL_CLASS_TO_PLOT_STATUS.get(fc, "normal")
        if status == "normal" and row.get("status") != "Normal":
            status = str(row.get("plot_status") or "flagged_unclassified")
        recs.append(
            {
                "Tag": tag,
                "Timestamp": ts,
                "Value": pd.to_numeric(row.get("tag_value"), errors="coerce"),
                "Status": status,
            }
        )
    if not recs:
        return pd.DataFrame(columns=["Tag", "Timestamp", "Value", "Status"])
    return pd.DataFrame(recs)


def filter_wide_through_day(wide: pd.DataFrame, selected_day: date) -> pd.DataFrame:
    cutoff = datetime.combine(selected_day, time.max)
    plot_df = wide.copy()
    ts = pd.to_datetime(plot_df[_TS_COL], errors="coerce")
    return plot_df.loc[ts.notna() & (ts <= cutoff)].copy()
