"""Daily scheduled anomaly jobs from DB datasets (drift + roots)."""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from . import db_queries
from . import db_repository as db_repo
from .anomaly_pipeline import compute_top10_roots_with_paths, run_drift_phase_from_prepared_wide
from .db_config import is_configured
from .db_dataset_loader import load_wide_timeseries_before_exclusive

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if not v:
        return default
    return v not in ("0", "false", "no", "off")


def schedule_start_utc_naive() -> datetime:
    raw = (os.environ.get("SCHEDULED_ANOMALY_START_UTC") or "2022-12-01T00:00:00").strip()
    dt = datetime.fromisoformat(raw.replace("Z", ""))
    return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def floor_day_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def current_day_utc_naive() -> datetime:
    return floor_day_utc_naive(datetime.now(timezone.utc))


def _plants_to_run_today() -> List[dict]:
    mapped = db_queries.list_plants_with_schedule_mappings()
    if mapped:
        return mapped
    ts_id = db_queries.get_latest_timeseries_dataset_id()
    causal_id = db_queries.get_latest_causal_dataset_id()
    fp = db_queries.first_plant_dataset_id()
    if fp and ts_id and causal_id:
        return [
            {
                "dataset_id": fp,
                "timeseries_dataset_id": int(ts_id),
                "causal_dataset_id": int(causal_id),
            }
        ]
    return []


def _run_single_scheduled_day_for_plant(
    day_bucket_utc_naive: datetime,
    plant_dataset_id: int,
    ts_id: int,
    causal_id: int,
) -> None:
    job_id = db_repo.scheduled_try_start_job(
        day_bucket_utc_naive, ts_id, causal_id, plant_dataset_id
    )
    if job_id is None:
        return

    paths = db_queries.fetch_causal_propagation_paths(int(causal_id))
    if not paths:
        db_repo.scheduled_finish_job_failed(
            job_id,
            "No propagation_path values found for the causal dataset.",
        )
        return

    range_end = day_bucket_utc_naive + timedelta(days=1)
    has_day_data = db_queries.timeseries_dataset_has_rows_in_range(
        int(ts_id), day_bucket_utc_naive, range_end
    )
    if not has_day_data:
        db_repo.scheduled_finish_job_skipped(
            job_id,
            "No timeseries observations for this day bucket (skipped).",
        )
        return

    try:
        wide = load_wide_timeseries_before_exclusive(int(ts_id), range_end)
    except Exception as e:
        logger.exception(
            "Scheduled daily run %s plant %s: timeseries load error: %s",
            day_bucket_utc_naive,
            plant_dataset_id,
            e,
        )
        db_repo.scheduled_finish_job_failed(job_id, f"Timeseries load error: {e}")
        return

    if wide.empty or "Timestamp" not in wide.columns:
        db_repo.scheduled_finish_job_skipped(
            job_id,
            "No usable time-series rows before this day window (skipped).",
        )
        return

    hist = float(os.environ.get("SCHEDULED_ANOMALY_HISTORIC_RATIO", "0.70"))
    lb = int(os.environ.get("SCHEDULED_ANOMALY_LOOKBACK_MONTHS", "2"))
    topk = int(os.environ.get("SCHEDULED_ANOMALY_TOP_K", "10"))
    try:
        out = run_drift_phase_from_prepared_wide(
            wide,
            paths,
            historic_ratio=hist,
            lookback_months=lb,
            top_n_drift_tags=topk,
        )
    except Exception as e:
        logger.exception(
            "Scheduled daily run %s plant %s failed: %s",
            day_bucket_utc_naive,
            plant_dataset_id,
            e,
        )
        db_repo.scheduled_finish_job_failed(job_id, str(e))
        return

    top_rows = out.get("top_drift_rows") or []
    summary = out.get("summary") or {}
    summary["Schedule_Type"] = "daily"
    summary["Daily_Run_Date"] = day_bucket_utc_naive.strftime("%Y-%m-%d")

    session_blob = out.get("session_blob") or {}
    tags = [str(t) for t in (out.get("top_target_tags") or [])]
    roots_by_tag = {}
    for t in tags:
        try:
            roots_by_tag[t] = compute_top10_roots_with_paths(session_blob, t)
        except Exception:
            roots_by_tag[t] = []

    try:
        db_repo.scheduled_finish_job_success(job_id, summary, top_rows)
        db_repo.scheduled_replace_roots(job_id, roots_by_tag)
    except Exception as e:
        logger.exception(
            "Scheduled daily run %s plant %s: persist failed: %s",
            day_bucket_utc_naive,
            plant_dataset_id,
            e,
        )
        db_repo.scheduled_finish_job_failed(job_id, f"Persist error: {e}")


def run_single_scheduled_day(day_bucket_utc_naive: datetime) -> None:
    if not _env_bool("SCHEDULED_ANOMALY_ENABLED", True):
        return
    if not is_configured():
        return

    day_bucket_utc_naive = floor_day_utc_naive(day_bucket_utc_naive)
    start = schedule_start_utc_naive()
    if day_bucket_utc_naive < start:
        return

    plants = _plants_to_run_today()
    if not plants:
        return

    for p in plants:
        pid = int(p["dataset_id"])
        ts_id = int(p["timeseries_dataset_id"])
        causal_id = int(p["causal_dataset_id"])
        try:
            _run_single_scheduled_day_for_plant(
                day_bucket_utc_naive, pid, ts_id, causal_id
            )
        except Exception:
            logger.exception(
                "Scheduled daily run %s: unhandled error for plant %s",
                day_bucket_utc_naive,
                pid,
            )


def catch_up_scheduled_days() -> None:
    if not _env_bool("SCHEDULED_ANOMALY_ENABLED", True):
        return
    if not is_configured():
        return
    try:
        db_repo.apply_schema_if_needed()
    except Exception as e:
        logger.warning("Scheduled anomaly schema apply skipped: %s", e)
        return

    start = schedule_start_utc_naive()
    plants = _plants_to_run_today()
    if not plants:
        return
    ts_ids = list({int(p["timeseries_dataset_id"]) for p in plants})
    max_obs_day: Optional[datetime] = None
    for tid in ts_ids:
        max_obs = db_queries.timeseries_dataset_max_observed_at(tid)
        if max_obs is None:
            continue
        d = floor_day_utc_naive(max_obs)
        if max_obs_day is None or d > max_obs_day:
            max_obs_day = d
    if max_obs_day is None:
        return

    last = db_queries.scheduled_max_finished_hour_bucket()
    h = (last + timedelta(days=1)) if last else start
    if h < start:
        h = start
    now_h = min(current_day_utc_naive(), max_obs_day)
    max_burst = int(os.environ.get("SCHEDULED_ANOMALY_BACKFILL_MAX", "500"))
    n = 0
    while h <= now_h and n < max_burst:
        # Missing days → skipped; errors → failed row; always advance to next calendar day.
        try:
            run_single_scheduled_day(h)
        except Exception:
            logger.exception("Scheduled catch-up: unhandled error for day %s", h)
        h += timedelta(days=1)
        n += 1


def five_minute_tick() -> None:
    # Run catch-up every 5 minutes so missing daily buckets are processed until today.
    catch_up_scheduled_days()


def start_backfill_thread() -> None:
    if not _env_bool("SCHEDULED_ANOMALY_ENABLED", True):
        return

    def _run() -> None:
        try:
            catch_up_scheduled_days()
        except Exception:
            logger.exception("catch_up_scheduled_days failed")

    t = threading.Thread(target=_run, name="scheduled-anomaly-catchup", daemon=True)
    t.start()
