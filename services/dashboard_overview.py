"""
Read-only aggregates for the main overview Dashboard page (/dashboard).
Keeps SQL localized so the UI can stay declarative and easy to extend.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymysql.err import ProgrammingError

from . import db_queries
from .db_config import get_connection, is_configured

logger = logging.getLogger(__name__)


def _scheduler_enabled() -> bool:
    v = (os.environ.get("SCHEDULED_ANOMALY_ENABLED") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _scheduled_status_counts(cur) -> Dict[str, int]:
    out: Dict[str, int] = {}
    try:
        cur.execute(
            """
            SELECT status, COUNT(*) AS c
            FROM scheduled_anomaly_job
            GROUP BY status
            """
        )
        for row in cur.fetchall():
            out[str(row[0] or "")] = int(row[1])
    except ProgrammingError:
        pass
    return out


def _scheduled_daily_completed(cur, start: date, end: date) -> List[Tuple[date, int]]:
    try:
        cur.execute(
            """
            SELECT DATE(hour_bucket) AS d, COUNT(*) AS c
            FROM scheduled_anomaly_job
            WHERE status = 'completed'
              AND DATE(hour_bucket) >= %s
              AND DATE(hour_bucket) <= %s
            GROUP BY DATE(hour_bucket)
            ORDER BY d ASC
            """,
            (start, end),
        )
        rows = cur.fetchall()
        return [(row[0], int(row[1])) for row in rows if row[0] is not None]
    except ProgrammingError:
        return []


def _last_scheduled_completed(cur) -> Optional[datetime]:
    try:
        cur.execute(
            """
            SELECT MAX(hour_bucket) FROM scheduled_anomaly_job
            WHERE status = 'completed'
            """
        )
        r = cur.fetchone()
        return r[0] if r and r[0] else None
    except ProgrammingError:
        return None


def _scalar_count(cur, sql: str) -> int:
    try:
        cur.execute(sql)
        r = cur.fetchone()
        return int(r[0]) if r and r[0] is not None else 0
    except ProgrammingError:
        return 0


def _safe_count_table(cur, table: str) -> Optional[int]:
    if not table or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", table):
        return None
    try:
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        return int(cur.fetchone()[0])
    except ProgrammingError:
        return None


def build_dashboard_snapshot() -> Dict[str, Any]:
    """
    Returns a plain dict for Jinja + optional Plotly (chart_labels / chart_values).
    All counts are best-effort; missing tables return zeros.
    """
    empty: Dict[str, Any] = {
        "ts_datasets": 0,
        "causal_datasets": 0,
        "ts_wide_rows_sum": 0,
        "ts_observation_rows": 0,
        "causal_rows": 0,
        "plants_total": 0,
        "plants_ready": 0,
        "scheduled_by_status": {},
        "scheduled_completed_total": 0,
        "last_scheduled_completed": None,
        "chart_labels": [],
        "chart_values": [],
        "anomaly_runs": 0,
        "outlier_runs": 0,
        "plant_upload_ts_rows": None,
        "plant_upload_causal_rows": None,
        "latest_anomaly_at": None,
        "latest_outlier_at": None,
        "scheduler_enabled": _scheduler_enabled(),
    }
    if not is_configured():
        return empty

    end = _utc_today()
    start = end - timedelta(days=13)
    chart_labels: List[str] = []
    chart_values: List[int] = []

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                by_status = _scheduled_status_counts(cur)
                daily = _scheduled_daily_completed(cur, start, end)
                last_c = _last_scheduled_completed(cur)

                dmap = {d: c for d, c in daily}
                for i in range(14):
                    d = start + timedelta(days=i)
                    chart_labels.append(d.isoformat())
                    chart_values.append(dmap.get(d, 0))

                ts_wide = _scalar_count(
                    cur,
                    "SELECT COALESCE(SUM(row_count), 0) FROM timeseries_dataset",
                )
                ts_obs = _scalar_count(cur, "SELECT COUNT(*) FROM timeseries_observation")
                c_rows = _scalar_count(cur, "SELECT COUNT(*) FROM causal_row")
                ano = _scalar_count(cur, "SELECT COUNT(*) FROM anomaly_run")
                out = _scalar_count(cur, "SELECT COUNT(*) FROM outlier_run")

                latest_a = None
                latest_o = None
                try:
                    cur.execute(
                        "SELECT MAX(created_at) FROM anomaly_run"
                    )
                    r = cur.fetchone()
                    latest_a = r[0].isoformat() if r and r[0] else None
                except ProgrammingError:
                    pass
                try:
                    cur.execute(
                        "SELECT MAX(created_at) FROM outlier_run"
                    )
                    r = cur.fetchone()
                    latest_o = r[0].isoformat() if r and r[0] else None
                except ProgrammingError:
                    pass

                put_ts = _safe_count_table(cur, "time_series_data")
                put_c = _safe_count_table(cur, "causal_data")

        plants = db_queries.list_plants_for_dashboard()
        mapped = db_queries.list_plants_with_schedule_mappings()
        completed_total = int(by_status.get("completed", 0))

        return {
            "ts_datasets": db_queries.count_timeseries_datasets(),
            "causal_datasets": db_queries.count_causal_datasets(),
            "ts_wide_rows_sum": ts_wide,
            "ts_observation_rows": ts_obs,
            "causal_rows": c_rows,
            "plants_total": len(plants),
            "plants_ready": len(mapped),
            "scheduled_by_status": by_status,
            "scheduled_completed_total": completed_total,
            "last_scheduled_completed": last_c.isoformat() if last_c else None,
            "chart_labels": chart_labels,
            "chart_values": chart_values,
            "anomaly_runs": ano,
            "outlier_runs": out,
            "plant_upload_ts_rows": put_ts,
            "plant_upload_causal_rows": put_c,
            "latest_anomaly_at": latest_a,
            "latest_outlier_at": latest_o,
            "scheduler_enabled": _scheduler_enabled(),
        }
    except Exception as e:
        logger.warning("build_dashboard_snapshot: %s", e)
        return empty
