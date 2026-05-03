"""Live Outlier detection page: V5 (Testing / part8) drift scores + correlation neighbors (no causal paths)."""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from .auto_without_causal_outlier_drift import (
    get_testing_v5_classify_long_df_from_wide,
    run_testing_deviation_spike_v5_outlier_drift,
)
from .db_dataset_loader import (
    load_wide_live_outlier_excel_dataset_before_exclusive,
    load_wide_timeseries_before_exclusive,
)
from .drift_detection_service import build_plot_figure_for_tag
from . import db_queries
from .part2_plots import build_part2_target_plot_json

logger = logging.getLogger(__name__)

_TS_COL = "Timestamp"
_MAX_DETAIL_ROWS_PER_TAG = 500
# Same mapping as ``auto_without_causal_outlier_drift._build_plot_inputs`` / part8 plot legend.
_FINAL_CLASS_TO_PLOT_STATUS: Dict[str, str] = {
    "Normal": "normal",
    "Drift": "sudden_jump",
    "Contextual Anomaly": "mild_outlier",
    "Drift + Anomaly": "mild_outlier",
    "Strong Anomaly": "strong_outlier",
}
_LIVE_OUTLIER_TOP_TAGS = 10
_STRONG_ANOMALY_CLASS = "Strong Anomaly"
_MAX_STRONG_OUTLIER_TAGS_LIST = 500


def live_outlier_drift_top_k() -> int:
    try:
        return max(5, int(os.environ.get("LIVE_OUTLIER_DRIFT_TOP_K", "50")))
    except (TypeError, ValueError):
        return 50


def live_outlier_related_tag_limit() -> int:
    try:
        return max(10, int(os.environ.get("LIVE_OUTLIER_RELATED_TAGS", "50")))
    except (TypeError, ValueError):
        return 50


def _day_bounds(day: date) -> Tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


def _v5_day_tag_scores(result_long: pd.DataFrame, day: date) -> pd.DataFrame:
    """Per-tag score for one calendar day from V5 long output (higher = more abnormal activity)."""
    day_start, day_end = _day_bounds(day)
    ts = pd.to_datetime(result_long["Timestamp"], errors="coerce")
    mask = ts.notna() & (ts >= day_start) & (ts < day_end)
    sub = result_long.loc[mask].copy()
    if sub.empty or "Tag" not in sub.columns:
        return pd.DataFrame(columns=["tag", "drift_score"])
    ab = sub["Final_Status"].astype(str).str.strip() == "Abnormal"
    sub["_ab"] = ab.astype(float)
    sub["_z"] = pd.to_numeric(sub.get("Abs_Value_Z"), errors="coerce").fillna(0.0)
    grp = sub.groupby("Tag", as_index=False).agg(
        n_ab=("_ab", "sum"),
        zsum=("Abs_Value_Z", lambda s: float(pd.to_numeric(s, errors="coerce").fillna(0.0).sum())),
    )
    grp["drift_score"] = grp["n_ab"].astype(float) + 1e-6 * grp["zsum"].astype(float)
    out = grp.rename(columns={"Tag": "tag"})[["tag", "drift_score"]]
    return out.sort_values("drift_score", ascending=False).reset_index(drop=True)


def _compute_v5_drift_rows_for_wide_day(wide: pd.DataFrame, day: date, log_ctx: str) -> List[Dict[str, Any]]:
    if wide.empty or _TS_COL not in wide.columns:
        return []
    try:
        long_v5 = get_testing_v5_classify_long_df_from_wide(wide, _TS_COL)
    except Exception as e:
        logger.exception("live outlier V5 %s day=%s: %s", log_ctx, day, e)
        return []
    ranked = _v5_day_tag_scores(long_v5, day)
    if ranked.empty:
        return []
    topk = live_outlier_drift_top_k()
    rows: List[Dict[str, Any]] = []
    for i, (_, row) in enumerate(ranked.head(topk).iterrows(), start=1):
        rows.append(
            {
                "rank": i,
                "tag": str(row["tag"]),
                "drift_score": float(row["drift_score"]),
            }
        )
    return rows


def compute_v5_drift_rows_for_plant_day(
    plant_dataset_id: int, day: date, timeseries_dataset_id: int
) -> List[Dict[str, Any]]:
    _, day_end = _day_bounds(day)
    try:
        wide = load_wide_timeseries_before_exclusive(int(timeseries_dataset_id), day_end)
    except Exception as e:
        logger.warning("live outlier wide load plant=%s day=%s: %s", plant_dataset_id, day, e)
        return []
    return _compute_v5_drift_rows_for_wide_day(
        wide, day, log_ctx=f"plant={plant_dataset_id}"
    )


def compute_v5_drift_rows_for_excel_dataset_day(excel_dataset_id: int, day: date) -> List[Dict[str, Any]]:
    _, day_end = _day_bounds(day)
    try:
        wide = load_wide_live_outlier_excel_dataset_before_exclusive(int(excel_dataset_id), day_end)
    except Exception as e:
        logger.warning("live outlier wide load excel=%s day=%s: %s", excel_dataset_id, day, e)
        return []
    return _compute_v5_drift_rows_for_wide_day(wide, day, log_ctx=f"excel={excel_dataset_id}")


def correlation_related_tags(
    wide_df: pd.DataFrame,
    tag: str,
    *,
    limit: int = 50,
    exclude: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Other tags sorted by |Pearson r| with ``tag`` (same shape as causal roots JSON)."""
    if wide_df.empty or tag not in wide_df.columns or _TS_COL not in wide_df.columns:
        return []
    ex = {tag, _TS_COL}
    if exclude:
        ex.update(str(x) for x in exclude)
    y = pd.to_numeric(wide_df[tag], errors="coerce")
    out: List[Tuple[str, float]] = []
    for col in wide_df.columns:
        if col in ex:
            continue
        x = pd.to_numeric(wide_df[col], errors="coerce")
        m = y.notna() & x.notna()
        if m.sum() < 5:
            continue
        r = float(y.loc[m].corr(x.loc[m]))
        if pd.isna(r):
            continue
        out.append((col, r))
    out.sort(key=lambda t: -abs(t[1]))
    lim = max(1, int(limit))
    return [
        {"root_cause": c, "root_cause_score": r, "propagation_path": ""}
        for c, r in out[:lim]
    ]


def _parse_display_ts_cell(s: Any) -> Optional[pd.Timestamp]:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    ts = pd.to_datetime(s, errors="coerce")
    if pd.isna(ts):
        return None
    return ts


def filter_part8_bundle_to_calendar_day(
    bundle: Dict[str, Any], day: date
) -> Dict[str, Any]:
    """Keep only rows whose timestamp falls in ``day`` (UTC-naive day window)."""
    day_start, day_end = _day_bounds(day)
    details_in = bundle.get("details_by_tag") or {}
    new_details: Dict[str, List[Dict[str, Any]]] = {}
    for tag, rows in details_in.items():
        kept: List[Dict[str, Any]] = []
        for r in rows:
            ts = _parse_display_ts_cell(r.get("Timestamp"))
            if ts is None:
                continue
            tsn = ts.to_pydatetime()
            if day_start <= tsn < day_end:
                kept.append(r)
        if len(kept) > _MAX_DETAIL_ROWS_PER_TAG:
            kept = kept[: _MAX_DETAIL_ROWS_PER_TAG]
        if kept:
            new_details[str(tag)] = kept

    tag_summaries: List[Dict[str, Any]] = []
    for tag, rows in new_details.items():
        tag_summaries.append(
            {
                "tag": tag,
                "status": str(rows[0].get("Final_Class") or ""),
                "drift_timestamp": rows[0].get("Timestamp"),
                "num_drift_points": int(len(rows)),
            }
        )
    top_tags = sorted(
        tag_summaries,
        key=lambda r: int(r.get("num_drift_points") or 0),
        reverse=True,
    )[:10]

    out = dict(bundle)
    out["details_by_tag"] = new_details
    out["tag_summaries"] = tag_summaries
    out["top_tags_by_points"] = top_tags
    out["monthly_pages_by_tag"] = {}
    return out


def build_part8_outlier_bundle_for_excel_dataset_day(
    excel_dataset_id: int, day: date
) -> Optional[Dict[str, Any]]:
    """
    Same pipeline as the Outlier detection tab (part8): ``run_testing_deviation_spike_v5_outlier_drift``,
    loaded from ``live_outlier_excel_*`` tables. Results are then restricted to the selected calendar day.
    """
    _, day_end = _day_bounds(day)
    try:
        wide = load_wide_live_outlier_excel_dataset_before_exclusive(int(excel_dataset_id), day_end)
    except Exception as e:
        logger.warning("part8 wide load excel=%s: %s", excel_dataset_id, e)
        return None
    if wide.empty or _TS_COL not in wide.columns:
        return None

    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
            tmp_path = tf.name
        wide.to_excel(tmp_path, index=False, engine="openpyxl")
        bundle = run_testing_deviation_spike_v5_outlier_drift(tmp_path)
    except Exception as e:
        logger.exception("part8 pipeline excel=%s day=%s: %s", excel_dataset_id, day, e)
        return None
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    try:
        return filter_part8_bundle_to_calendar_day(bundle, day)
    except Exception as e:
        logger.exception("filter part8 bundle: %s", e)
        return None


def part8_bundle_light_for_template(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Drop large DataFrames; keep JSON-friendly summary + tables for Jinja."""
    df = bundle.get("df_for_script")
    plot_tags: List[str] = []
    if df is not None and hasattr(df, "columns"):
        plot_tags = sorted(str(c) for c in df.columns if str(c) != _TS_COL)
    return {
        "summary": bundle.get("summary") or {},
        "tag_summaries": bundle.get("tag_summaries") or [],
        "top_tags_by_points": bundle.get("top_tags_by_points") or [],
        "details_by_tag": bundle.get("details_by_tag") or {},
        "tag_limits_by_tag": bundle.get("tag_limits_by_tag") or {},
        "x_variables_by_tag": bundle.get("x_variables_by_tag") or {},
        "timestamp_summary_rows": bundle.get("timestamp_summary_rows") or [],
        "plot_tag_names": plot_tags,
    }


def plant_row_for_live_outlier(plant_dataset_id: int) -> Optional[Dict[str, Any]]:
    for p in db_queries.list_plants_for_dashboard():
        if int(p["dataset_id"]) == int(plant_dataset_id):
            return p
    return None


def _out_df_from_stored_live_outlier_detail(
    rows: List[Dict[str, Any]], tag: str
) -> pd.DataFrame:
    """Build long ``out_df`` for :func:`build_plot_figure_for_tag` from DB detail rows."""
    tag = str(tag)
    recs: List[Dict[str, Any]] = []
    for r in rows:
        obs = r.get("observed_at")
        if obs is None:
            continue
        ts = pd.to_datetime(obs, errors="coerce")
        if pd.isna(ts):
            continue
        fc = str(r.get("final_class") or "").strip()
        status = _FINAL_CLASS_TO_PLOT_STATUS.get(fc, "normal")
        recs.append(
            {
                "Tag": tag,
                "Timestamp": ts,
                "Value": pd.to_numeric(r.get("actual_value"), errors="coerce"),
                "Status": status,
            }
        )
    if not recs:
        return pd.DataFrame(columns=["Tag", "Timestamp", "Value", "Status"])
    return pd.DataFrame(recs)


def _plot_time_bounds_from_wide(plot_df: pd.DataFrame) -> Optional[Tuple[datetime, datetime]]:
    if plot_df.empty or _TS_COL not in plot_df.columns:
        return None
    ts_series = pd.to_datetime(plot_df[_TS_COL], errors="coerce")
    ts_ok = ts_series.dropna()
    if ts_ok.empty:
        return None
    t_lo = ts_ok.min()
    t_hi = ts_ok.max()
    if isinstance(t_lo, pd.Timestamp):
        t_lo = t_lo.to_pydatetime()
    if isinstance(t_hi, pd.Timestamp):
        t_hi = t_hi.to_pydatetime()
    return t_lo, t_hi


def build_live_outlier_plant_day_tag_detail(
    plant_dataset_id: int,
    day: date,
    tag: str,
    *,
    selected_day: Optional[date] = None,
    compare_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    tag = (tag or "").strip()
    if not tag:
        raise ValueError("Missing tag.")
    plant = plant_row_for_live_outlier(int(plant_dataset_id))
    if not plant:
        raise ValueError("Plant not found.")
    ts_id = plant.get("timeseries_dataset_id")
    if not ts_id:
        raise ValueError("Plant has no timeseries_dataset_id mapping.")

    _, day_end = _day_bounds(day)
    plot_df = load_wide_timeseries_before_exclusive(int(ts_id), day_end)
    if plot_df.empty or _TS_COL not in plot_df.columns:
        raise ValueError("No time-series data available for this day window.")
    if tag not in plot_df.columns:
        raise ValueError("Selected tag is not available in this plant's time-series data.")

    drift_rows = compute_v5_drift_rows_for_plant_day(
        int(plant_dataset_id), day, int(ts_id)
    )
    allowed = {str(r["tag"]) for r in drift_rows}
    if tag not in allowed:
        raise ValueError("This tag is not in this day's V5 drift list.")

    if selected_day is not None and _TS_COL in plot_df.columns:
        cutoff = datetime.combine(selected_day, time.max)
        ts = pd.to_datetime(plot_df[_TS_COL], errors="coerce")
        plot_df = plot_df.loc[ts.notna() & (ts <= cutoff)].copy()

    wide_for_corr = load_wide_timeseries_before_exclusive(int(ts_id), day_end)
    roots = correlation_related_tags(
        wide_for_corr,
        tag,
        limit=live_outlier_related_tag_limit(),
    )

    plot_json = build_part2_target_plot_json(
        plot_df,
        _TS_COL,
        tag,
        None,
        compare_tags=compare_tags or [],
    )

    drift_score: Optional[float] = None
    for r in drift_rows:
        if str(r.get("tag", "")) == tag:
            drift_score = r.get("drift_score")
            break

    job_like = {
        "id": None,
        "plant_dataset_id": int(plant_dataset_id),
        "timeseries_dataset_id": int(ts_id),
        "hour_bucket": datetime.combine(day, time.min),
        "status": "completed",
        "summary": {},
    }

    return {
        "job": job_like,
        "plant_name": plant.get("plant_name"),
        "tag": tag,
        "plot_json": plot_json,
        "roots": roots,
        "roots_error": None,
        "drift_score": drift_score,
        "summary": {},
    }


def build_live_outlier_excel_day_tag_detail(
    excel_dataset_id: int,
    day: date,
    tag: str,
    *,
    selected_day: Optional[date] = None,
    compare_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    tag = (tag or "").strip()
    if not tag:
        raise ValueError("Missing tag.")
    meta = db_queries.live_outlier_excel_dataset_by_id(int(excel_dataset_id))
    if not meta:
        raise ValueError("Dataset not found.")

    _, day_end = _day_bounds(day)
    plot_df = load_wide_live_outlier_excel_dataset_before_exclusive(int(excel_dataset_id), day_end)
    if plot_df.empty or _TS_COL not in plot_df.columns:
        raise ValueError("No time-series data available for this day window.")
    if tag not in plot_df.columns:
        raise ValueError("Selected tag is not available in this dataset.")

    run = db_queries.latest_live_outlier_analysis_run(int(excel_dataset_id))
    if run and str(run.get("status") or "") == "failed":
        raise ValueError(
            "Latest outlier analysis for this dataset failed. Re-upload the Excel file or check server logs."
        )
    day_start, day_end_excl = _day_bounds(day)
    drift_score: Optional[float] = None
    if run and str(run.get("status") or "") == "completed":
        allowed = db_queries.live_outlier_distinct_tags_for_run_day(
            int(run["id"]), day_start, day_end_excl
        )
        if tag not in allowed:
            raise ValueError(
                "This tag is not in stored outlier results for the selected UTC day."
            )
        n = db_queries.live_outlier_tag_row_count_for_run_day(
            int(run["id"]), day_start, day_end_excl, tag
        )
        drift_score = float(n)
    else:
        drift_rows = compute_v5_drift_rows_for_excel_dataset_day(int(excel_dataset_id), day)
        allowed = {str(r["tag"]) for r in drift_rows}
        if tag not in allowed:
            raise ValueError("This tag is not in this day's V5 drift list.")
        for r in drift_rows:
            if str(r.get("tag", "")) == tag:
                drift_score = r.get("drift_score")
                break

    if selected_day is not None and _TS_COL in plot_df.columns:
        cutoff = datetime.combine(selected_day, time.max)
        ts = pd.to_datetime(plot_df[_TS_COL], errors="coerce")
        plot_df = plot_df.loc[ts.notna() & (ts <= cutoff)].copy()

    ct = [str(x).strip() for x in (compare_tags or []) if str(x).strip()]
    wide_for_corr = load_wide_live_outlier_excel_dataset_before_exclusive(
        int(excel_dataset_id), day_end
    )
    roots: List[Dict[str, Any]] = correlation_related_tags(
        wide_for_corr,
        tag,
        limit=live_outlier_related_tag_limit(),
    )

    plot_json: str
    bounds = _plot_time_bounds_from_wide(plot_df)
    if (
        run
        and str(run.get("status") or "") == "completed"
        and bounds is not None
    ):
        t_lo, t_hi = bounds
        detail_rows = db_queries.fetch_live_outlier_detail_rows_for_tag_time_range(
            int(run["id"]), tag, t_lo, t_hi
        )
        out_df = _out_df_from_stored_live_outlier_detail(detail_rows, tag)
        try:
            plot_json = build_plot_figure_for_tag(
                plot_df, out_df, tag, compare_tags=ct
            ).to_json()
        except ValueError:
            plot_json = build_part2_target_plot_json(
                plot_df,
                _TS_COL,
                tag,
                None,
                compare_tags=ct,
            )
    else:
        plot_json = build_part2_target_plot_json(
            plot_df,
            _TS_COL,
            tag,
            None,
            compare_tags=ct,
        )

    job_like = {
        "id": None,
        "plant_dataset_id": None,
        "timeseries_dataset_id": None,
        "excel_dataset_id": int(excel_dataset_id),
        "hour_bucket": datetime.combine(day, time.min),
        "status": "completed",
        "summary": {},
    }

    return {
        "job": job_like,
        "plant_name": str(meta.get("dataset_name") or ""),
        "tag": tag,
        "plot_json": plot_json,
        "roots": roots,
        "roots_error": None,
        "drift_score": drift_score,
        "summary": {},
    }


def _fmt_ts_display(ts: Any) -> str:
    if ts is None:
        return ""
    if isinstance(ts, datetime):
        return ts.strftime("%m/%d/%Y %H:%M")
    tsn = pd.to_datetime(ts, errors="coerce")
    if pd.isna(tsn):
        return str(ts)
    return tsn.strftime("%m/%d/%Y %H:%M")


def build_part8_display_from_stored_analysis(
    dataset_id: int, day: date
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], bool, Optional[str]]:
    """
    Build the same ``part8_display`` shape as ``part8_bundle_light_for_template``, using
    persisted analysis rows for ``dataset_id`` filtered to ``day`` (UTC calendar window).

    ``drifts`` / ``has_outlier_day`` reflect tags with at least one **Strong Anomaly** row
    that day (sorted by strong point count). Other part8 fields still use the top-by-points slice.
    Returns (part8_light_dict, drifts_for_json, has_outlier_day, error_message).
    """
    run = db_queries.latest_live_outlier_analysis_run(int(dataset_id))
    if not run:
        return None, [], False, None
    if str(run.get("status") or "") == "failed":
        return None, [], False, str(run.get("error_message") or "Analysis failed.")

    day_start, day_end_excl = _day_bounds(day)
    rows = db_queries.fetch_live_outlier_detail_rows_for_day(
        int(run["id"]), day_start, day_end_excl
    )
    # Group with raw timestamps, then sort per tag in Python (avoids MySQL ORDER BY sort-buffer errors).
    raw_by_tag: Dict[str, List[Tuple[Any, Dict[str, Any]]]] = {}
    for r in rows:
        tname = str(r.get("tag_name") or "")
        if not tname:
            continue
        obs_at = r.get("observed_at")
        ts_sort = pd.to_datetime(obs_at, errors="coerce")
        if pd.isna(ts_sort):
            ts_sort = pd.Timestamp.min
        row_out = {
            "Timestamp": _fmt_ts_display(obs_at),
            "Actual_Value": r.get("actual_value"),
            "Predicted_Value": r.get("predicted_value"),
            "Final_Class": r.get("final_class"),
            "Direction": r.get("direction"),
            "Reason": r.get("reason") or "",
        }
        raw_by_tag.setdefault(tname, []).append((ts_sort, row_out))

    details_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    for tname, pairs in raw_by_tag.items():
        pairs.sort(key=lambda x: x[0], reverse=True)
        lst = [p[1] for p in pairs]
        if len(lst) > _MAX_DETAIL_ROWS_PER_TAG:
            lst = lst[:_MAX_DETAIL_ROWS_PER_TAG]
        details_by_tag[tname] = lst

    has_detail_rows_for_day = bool(details_by_tag)
    strong_outlier_tags: List[Dict[str, Any]] = []
    for tname, lst in details_by_tag.items():
        n_strong = sum(
            1
            for row in lst
            if str(row.get("Final_Class") or "").strip() == _STRONG_ANOMALY_CLASS
        )
        if n_strong > 0:
            strong_outlier_tags.append({"tag": tname, "strong_count": int(n_strong)})
    strong_outlier_tags.sort(key=lambda x: (-int(x["strong_count"]), str(x["tag"])))
    strong_outlier_tags = strong_outlier_tags[:_MAX_STRONG_OUTLIER_TAGS_LIST]

    tag_summaries: List[Dict[str, Any]] = []
    for tname, lst in details_by_tag.items():
        if not lst:
            continue
        first = lst[-1]
        tag_summaries.append(
            {
                "tag": tname,
                "status": str(first.get("Final_Class") or ""),
                "drift_timestamp": first.get("Timestamp") or "",
                "num_drift_points": int(len(lst)),
            }
        )
    ranked = sorted(
        tag_summaries,
        key=lambda s: int(s.get("num_drift_points") or 0),
        reverse=True,
    )
    top_slice = ranked[:_LIVE_OUTLIER_TOP_TAGS]
    top_tag_names = {str(s.get("tag") or "") for s in top_slice if s.get("tag")}
    details_trimmed = {k: v for k, v in details_by_tag.items() if k in top_tag_names}

    artifacts = run.get("artifacts_json") or {}
    if not isinstance(artifacts, dict):
        artifacts = {}
    summary = run.get("summary_json") or {}
    if not isinstance(summary, dict):
        summary = {}

    first_strong = str(strong_outlier_tags[0]["tag"]) if strong_outlier_tags else ""
    part8_light: Dict[str, Any] = {
        "summary": summary,
        "tag_summaries": top_slice,
        "top_tags_by_points": top_slice,
        "details_by_tag": details_trimmed,
        "tag_limits_by_tag": artifacts.get("tag_limits_by_tag") or {},
        "x_variables_by_tag": artifacts.get("x_variables_by_tag") or {},
        "timestamp_summary_rows": artifacts.get("timestamp_summary_rows") or [],
        "plot_tag_names": artifacts.get("plot_tag_names") or [],
        "first_drift_tag": first_strong or (str(top_slice[0].get("tag") or "") if top_slice else ""),
        "remaining_tags_list": [str(s.get("tag") or "") for s in top_slice[1:_LIVE_OUTLIER_TOP_TAGS] if s.get("tag")],
        "strong_outlier_tags": strong_outlier_tags,
        "has_detail_rows_for_day": has_detail_rows_for_day,
    }
    drifts = [
        {
            "rank": i,
            "tag": str(s["tag"]),
            "drift_score": float(s["strong_count"]),
        }
        for i, s in enumerate(strong_outlier_tags, start=1)
    ]
    has_outlier_day = len(drifts) > 0
    return part8_light, drifts, has_outlier_day, None
