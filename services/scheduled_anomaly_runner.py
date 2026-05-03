"""Daily scheduled anomaly jobs from DB datasets (drift + roots)."""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

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
) -> bool:
    """
    Process one calendar day for one plant. Idempotent: completed jobs are no-ops.

    Returns True if a new job was started and finished (success/skip/fail path ran), False if
    this bucket was already completed or another worker holds a young running row.
    """
    job_id = db_repo.scheduled_try_start_job(
        day_bucket_utc_naive, ts_id, causal_id, plant_dataset_id
    )
    if job_id is None:
        return False

    paths = db_queries.fetch_causal_propagation_paths(int(causal_id))
    if not paths:
        db_repo.scheduled_finish_job_failed(
            job_id,
            "No propagation_path values found for the causal dataset.",
        )
        return True

    range_end = day_bucket_utc_naive + timedelta(days=1)
    has_day_data = db_queries.timeseries_dataset_has_rows_in_range(
        int(ts_id), day_bucket_utc_naive, range_end
    )
    if not has_day_data:
        db_repo.scheduled_finish_job_skipped(
            job_id,
            "No timeseries observations for this day bucket (skipped).",
        )
        return True

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
        return True

    if wide.empty or "Timestamp" not in wide.columns:
        db_repo.scheduled_finish_job_skipped(
            job_id,
            "No usable time-series rows before this day window (skipped).",
        )
        return True

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
        return True

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
    return True


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


def _plant_catchup_bounds(
    plant_dataset_id: int, ts_id: int
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Returns (next_day, last_data_day_cap) in UTC-naive day starts.
    next_day is None if there is no observation data for this timeseries dataset.

    Per plant: next_day is the calendar day after the latest completed/skipped job for
    that plant only. If this plant has no such jobs yet, next_day is the first calendar
    day that has observations for this plant's mapped timeseries dataset ("day one").
    """
    min_obs = db_queries.timeseries_dataset_min_observed_at(int(ts_id))
    max_obs = db_queries.timeseries_dataset_max_observed_at(int(ts_id))
    if min_obs is None or max_obs is None:
        return None, None
    first_day = floor_day_utc_naive(min_obs)
    last_data_day = floor_day_utc_naive(max_obs)
    last_proc = db_queries.scheduled_max_processed_hour_bucket_for_plant(int(plant_dataset_id))
    if last_proc is None:
        next_day = first_day
    else:
        nxt = floor_day_utc_naive(last_proc) + timedelta(days=1)
        next_day = max(nxt, first_day)
    cap_day = min(current_day_utc_naive(), last_data_day)
    return next_day, cap_day


def run_live_dashboard_catchup(
    *,
    plant_dataset_id: Optional[int] = None,
    max_day_runs: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Incremental live-dashboard processing: each plant has its own calendar cursor in
    ``scheduled_anomaly_job`` (completed/skipped advance; failed is retried).

    Stops at the last calendar day that has timeseries observations. Missing intra-range days
    produce ``skipped`` jobs and do not fail the run.

    Args:
        plant_dataset_id: If set, only this plant; otherwise all mapped plants.
        max_day_runs: Global cap on job starts this invocation (default env). Use ``1`` for the
            UI “Process next day” button (one calendar day per click).
    """
    summary: Dict[str, Any] = {
        "ok": True,
        "day_runs_started": 0,
        "plants": [],
        "message": "",
    }
    if not _env_bool("SCHEDULED_ANOMALY_ENABLED", True):
        summary["ok"] = False
        summary["message"] = "SCHEDULED_ANOMALY_ENABLED is off."
        return summary
    if not is_configured():
        summary["ok"] = False
        summary["message"] = "Database not configured."
        return summary
    try:
        db_repo.apply_schema_if_needed()
    except Exception as e:
        logger.warning("Live dashboard catch-up: schema apply skipped: %s", e)
        summary["ok"] = False
        summary["message"] = f"Schema error: {e}"
        return summary

    burst = max_day_runs if max_day_runs is not None else int(
        os.environ.get("SCHEDULED_ANOMALY_BACKFILL_MAX", "500")
    )
    plants = _plants_to_run_today()
    if plant_dataset_id is not None:
        pid_f = int(plant_dataset_id)
        plants = [p for p in plants if int(p["dataset_id"]) == pid_f]
        if not plants:
            summary["ok"] = False
            summary["message"] = f"No mapped plant with dataset_id={pid_f}."
            return summary

    runs = 0
    for p in plants:
        pid = int(p["dataset_id"])
        ts_id = int(p["timeseries_dataset_id"])
        causal_id = int(p["causal_dataset_id"])
        entry: Dict[str, Any] = {
            "plant_dataset_id": pid,
            "status": "idle",
            "next_day": None,
            "through_cap": None,
            "runs_this_tick": 0,
        }
        next_day, cap_day = _plant_catchup_bounds(pid, ts_id)
        entry["through_cap"] = cap_day.date().isoformat() if cap_day else None
        if next_day is None or cap_day is None:
            entry["status"] = "no_timeseries_observations"
            summary["plants"].append(entry)
            continue
        entry["next_day"] = next_day.date().isoformat()
        if next_day > cap_day:
            entry["status"] = "caught_up"
            summary["plants"].append(entry)
            continue

        # Manual "Process next day": one HTTP click must touch at most this plant's
        # cursor day only (last completed/skipped + 1, or first observation day). Do not
        # walk forward across multiple calendar days in a single request.
        if burst == 1:
            d = next_day
            entry["cursor_day"] = d.date().isoformat()
            last_proc = db_queries.scheduled_max_processed_hour_bucket_for_plant(pid)
            if last_proc is not None:
                entry["last_processed_day"] = floor_day_utc_naive(last_proc).date().isoformat()
            row = db_queries.scheduled_job_row_by_bucket(d, pid)
            st = str(row["status"]) if row else ""
            if st in ("completed", "skipped"):
                entry["status"] = "already_processed"
            else:
                # Include status == "running": scheduled_try_start_job reclaims stale runs
                # (>120 min) so a crashed worker does not block forever.
                pre_st = st
                try:
                    did_run = _run_single_scheduled_day_for_plant(d, pid, ts_id, causal_id)
                    if did_run:
                        runs += 1
                        entry["runs_this_tick"] = 1
                        entry["status"] = "processed"
                    elif pre_st == "running":
                        logger.info(
                            "Plant %s day %s: job still running (young lease).",
                            pid,
                            d.date(),
                        )
                        entry["status"] = "blocked_running"
                    else:
                        entry["status"] = "noop"
                except Exception:
                    logger.exception("Catch-up plant %s day %s", pid, d.date())
                    entry["status"] = "error"
            summary["plants"].append(entry)
            continue

        d = next_day
        blocked_running = False
        while d <= cap_day and runs < burst:
            row = db_queries.scheduled_job_row_by_bucket(d, pid)
            st = str(row["status"]) if row else ""
            if st in ("completed", "skipped"):
                d += timedelta(days=1)
                continue
            pre_st = st
            try:
                did_run = _run_single_scheduled_day_for_plant(d, pid, ts_id, causal_id)
                if did_run:
                    runs += 1
                    entry["runs_this_tick"] += 1
                elif pre_st == "running":
                    logger.info(
                        "Plant %s day %s: job still running (young lease); deferring catch-up.",
                        pid,
                        d.date(),
                    )
                    blocked_running = True
                    break
            except Exception:
                logger.exception("Catch-up plant %s day %s", pid, d.date())
            d += timedelta(days=1)
        if entry["runs_this_tick"]:
            entry["status"] = "processed"
        elif blocked_running:
            entry["status"] = "blocked_running"
        elif d > cap_day:
            entry["status"] = "caught_up"
        else:
            entry["status"] = "noop"
        summary["plants"].append(entry)

    summary["day_runs_started"] = runs
    if max_day_runs == 1:
        if runs == 0:
            statuses = [str(p.get("status") or "") for p in summary["plants"]]
            if "already_processed" in statuses:
                summary["message"] = (
                    "No new day started: the next calendar day for this plant was already completed or skipped."
                )
            elif "blocked_running" in statuses:
                summary["message"] = (
                    "No new day started: that calendar day is still running; try again later."
                )
            else:
                summary["message"] = (
                    "No new day was started (already caught up, nothing pending, or an error)."
                )
        else:
            summary["message"] = "Processed the next calendar day (drift run, skipped no-data day, or recorded failure)."
    else:
        summary["message"] = f"Started {runs} day job(s) this tick."
    return summary


def catch_up_scheduled_days() -> None:
    """Backward-compatible name: run incremental per-plant catch-up."""
    run_live_dashboard_catchup()


def live_dashboard_scheduler_tick() -> None:
    run_live_dashboard_catchup()


def five_minute_tick() -> None:
    """Legacy job id; delegates to hourly/live-dashboard tick."""
    live_dashboard_scheduler_tick()


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
