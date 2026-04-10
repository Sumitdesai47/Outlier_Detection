"""Build per-tag detail from scheduled anomaly results (drift plot + stored roots)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from .db_dataset_loader import load_wide_timeseries_before_exclusive
from . import db_queries
from .part2_plots import build_part2_target_plot_json


def build_scheduled_job_tag_detail(
    job_id: int,
    tag: str,
    selected_day: Optional[date] = None,
    compare_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Load scheduled job and return a fast DB-backed plot + stored roots.
    """
    tag = (tag or "").strip()
    if not tag:
        raise ValueError("Missing tag.")

    job = db_queries.scheduled_job_by_id(job_id)
    if not job:
        raise ValueError("Job not found.")
    if str(job.get("status") or "") != "completed":
        raise ValueError("This job has no completed drift results to display.")

    drift_rows = db_queries.scheduled_drift_rows_for_job(job_id)
    stored_tags = [str(r.get("tag", "")) for r in drift_rows]
    if tag not in stored_tags:
        raise ValueError("This tag is not part of this daily run’s top drift list.")

    ts_id = job.get("timeseries_dataset_id")
    if not ts_id:
        raise ValueError("Job is missing time-series dataset reference.")

    day_bucket = job.get("hour_bucket")
    if day_bucket is None:
        raise ValueError("Job is missing day bucket.")

    summary = job.get("summary") if isinstance(job.get("summary"), dict) else {}
    range_end = day_bucket + timedelta(days=1)
    plot_df = load_wide_timeseries_before_exclusive(int(ts_id), range_end)
    ts_col = "Timestamp"
    if plot_df.empty or ts_col not in plot_df.columns:
        raise ValueError("No time-series data available for this job window.")
    if tag not in plot_df.columns:
        raise ValueError("Selected tag is not available in this job's time-series data.")

    # Keep function signature compatibility with plotting utility.
    drift_time_raw = None

    if selected_day is not None and ts_col in plot_df.columns:
        cutoff = datetime.combine(selected_day, time.max)
        ts = pd.to_datetime(plot_df[ts_col], errors="coerce")
        plot_df = plot_df.loc[ts.notna() & (ts <= cutoff)].copy()

    roots: List[Dict[str, Any]] = []
    roots_error: Optional[str] = None
    try:
        rows = db_queries.scheduled_root_rows_for_job_tag(job_id, tag)
        roots = [
            {
                "root_cause": str(r.get("root_cause_tag", "")),
                "root_cause_score": r.get("root_cause_score"),
                "propagation_path": str(r.get("propagation_path", "")),
            }
            for r in rows
        ]
    except Exception as e:
        roots_error = str(e)

    plot_json = build_part2_target_plot_json(
        plot_df,
        ts_col,
        tag,
        drift_time_raw,
        compare_tags=compare_tags or [],
    )

    drift_score: Optional[float] = None
    for r in drift_rows:
        if str(r.get("tag", "")) == tag:
            drift_score = r.get("drift_score")
            break

    return {
        "job": job,
        "tag": tag,
        "plot_json": plot_json,
        "roots": roots,
        "roots_error": roots_error,
        "drift_score": drift_score,
        "summary": summary,
    }
