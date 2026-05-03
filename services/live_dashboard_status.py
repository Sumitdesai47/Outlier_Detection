"""
Live Dashboard: per-plant calendar / catch-up status for API and templates.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List

from . import db_queries
from .db_config import is_configured
from .scheduled_anomaly_runner import _plant_catchup_bounds, floor_day_utc_naive

# Calendar heatmap: limit distinct completed days returned (full list still in DB).
_DEFAULT_COMPLETED_DAY_LIMIT = int(
    os.environ.get("LIVE_DASHBOARD_STATUS_DAY_LIMIT", "366")
)


def build_plant_live_status(plant_dataset_id: int) -> Dict[str, Any]:
    """
    Snapshot for one plant: mapping, observation bounds, processed cursor, completed days.

    Used by Live Dashboard UI (populate button, calendar hints) and optional JSON API.
    """
    empty: Dict[str, Any] = {
        "plant_dataset_id": plant_dataset_id,
        "plant_name": None,
        "has_mapping": False,
        "timeseries_dataset_id": None,
        "causal_dataset_id": None,
        "scheduler_enabled": _scheduler_enabled_flag(),
        "data_first_day": None,
        "data_last_day": None,
        "last_processed_day": None,
        "has_running_job": False,
        "running_day": None,
        "next_catchup_day": None,
        "catchup_cap_day": None,
        "caught_up": False,
        "completed_days_iso": [],
        "completed_count": 0,
        "show_manual_populate": False,
    }
    if not is_configured():
        return empty

    plants = db_queries.list_plants_for_dashboard()
    by_id = {int(p["dataset_id"]): p for p in plants}
    p = by_id.get(int(plant_dataset_id))
    if not p:
        return empty

    empty["plant_name"] = p.get("plant_name")
    ts_id = p.get("timeseries_dataset_id")
    causal_id = p.get("causal_dataset_id")
    has_mapping = ts_id is not None and causal_id is not None
    empty["has_mapping"] = has_mapping
    empty["timeseries_dataset_id"] = int(ts_id) if ts_id is not None else None
    empty["causal_dataset_id"] = int(causal_id) if causal_id is not None else None

    if not has_mapping:
        return empty

    min_obs = db_queries.timeseries_dataset_min_observed_at(int(ts_id))
    max_obs = db_queries.timeseries_dataset_max_observed_at(int(ts_id))
    if min_obs is not None:
        empty["data_first_day"] = floor_day_utc_naive(min_obs).date().isoformat()
    if max_obs is not None:
        empty["data_last_day"] = floor_day_utc_naive(max_obs).date().isoformat()

    last_proc = db_queries.scheduled_max_processed_hour_bucket_for_plant(
        int(plant_dataset_id)
    )
    if last_proc is not None:
        empty["last_processed_day"] = floor_day_utc_naive(last_proc).date().isoformat()
    running_bucket = db_queries.scheduled_latest_running_hour_bucket_for_plant(
        int(plant_dataset_id)
    )
    if running_bucket is not None:
        empty["has_running_job"] = True
        empty["running_day"] = floor_day_utc_naive(running_bucket).date().isoformat()

    next_d, cap_d = _plant_catchup_bounds(int(plant_dataset_id), int(ts_id))
    if next_d is not None:
        empty["next_catchup_day"] = next_d.date().isoformat()
    if cap_d is not None:
        empty["catchup_cap_day"] = cap_d.date().isoformat()
    if next_d is not None and cap_d is not None:
        empty["caught_up"] = next_d > cap_d

    lim = max(1, _DEFAULT_COMPLETED_DAY_LIMIT)
    completed_dates: List[date] = db_queries.scheduled_list_completed_days_for_plant(
        int(plant_dataset_id), lim
    )
    empty["completed_count"] = len(completed_dates)
    empty["completed_days_iso"] = [d.isoformat() for d in completed_dates]

    # Encourage manual run when nothing completed yet but data + mapping exist.
    empty["show_manual_populate"] = bool(
        has_mapping
        and min_obs is not None
        and not empty["caught_up"]
        and empty["completed_count"] == 0
    )
    return empty


def _scheduler_enabled_flag() -> bool:
    v = (os.environ.get("SCHEDULED_ANOMALY_ENABLED") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")
