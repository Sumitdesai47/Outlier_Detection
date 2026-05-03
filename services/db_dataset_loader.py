"""Build wide time-series DataFrames from MySQL observations (for scheduled jobs)."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from .db_config import get_connection, is_configured


def load_wide_timeseries_before_exclusive(
    dataset_id: int, range_end_exclusive_utc_naive: datetime
) -> pd.DataFrame:
    """
    Pivot long observations to wide format with a Timestamp column.
    Keeps rows where observed_at is not null and observed_at < range_end_exclusive (naive UTC).
    """
    if not is_configured():
        return pd.DataFrame()
    with get_connection() as conn:
        # No ORDER BY: MySQL can exhaust sort_buffer on large datasets (errno 1038).
        obs = pd.read_sql(
            """
            SELECT row_index, observed_at, tag_name, value
            FROM timeseries_observation
            WHERE dataset_id = %s
            """,
            conn,
            params=[dataset_id],
        )
    if obs.empty:
        return pd.DataFrame()
    obs = obs.sort_values(["row_index", "tag_name"], kind="mergesort")
    obs["observed_at"] = pd.to_datetime(obs["observed_at"], errors="coerce", utc=True)
    obs["observed_at"] = obs["observed_at"].dt.tz_localize(None)
    end = pd.Timestamp(range_end_exclusive_utc_naive)
    obs = obs[obs["observed_at"].notna() & (obs["observed_at"] < end)]
    if obs.empty:
        return pd.DataFrame()
    pivot = obs.pivot_table(index="row_index", columns="tag_name", values="value", aggfunc="first")
    ts_col = obs.groupby("row_index")["observed_at"].min()
    wide = pivot.join(ts_col.rename("Timestamp"), how="inner")
    wide = wide.reset_index(drop=True)
    front = ["Timestamp"] + [c for c in wide.columns if c != "Timestamp"]
    return wide[front].copy()


def load_wide_live_outlier_excel_dataset_before_exclusive(
    dataset_id: int, range_end_exclusive_utc_naive: datetime
) -> pd.DataFrame:
    """
    Pivot long observations from ``live_outlier_excel_observation`` to wide (Timestamp + tags).
    Same row filter contract as ``load_wide_timeseries_before_exclusive``.
    """
    if not is_configured():
        return pd.DataFrame()
    with get_connection() as conn:
        # No ORDER BY: MySQL can exhaust sort_buffer on large datasets (errno 1038).
        obs = pd.read_sql(
            """
            SELECT row_index, observed_at, tag_name, value
            FROM live_outlier_excel_observation
            WHERE dataset_id = %s
            """,
            conn,
            params=[dataset_id],
        )
    if obs.empty:
        return pd.DataFrame()
    obs = obs.sort_values(["row_index", "tag_name"], kind="mergesort")
    obs["observed_at"] = pd.to_datetime(obs["observed_at"], errors="coerce", utc=True)
    obs["observed_at"] = obs["observed_at"].dt.tz_localize(None)
    end = pd.Timestamp(range_end_exclusive_utc_naive)
    obs = obs[obs["observed_at"].notna() & (obs["observed_at"] < end)]
    if obs.empty:
        return pd.DataFrame()
    pivot = obs.pivot_table(index="row_index", columns="tag_name", values="value", aggfunc="first")
    ts_col = obs.groupby("row_index")["observed_at"].min()
    wide = pivot.join(ts_col.rename("Timestamp"), how="inner")
    wide = wide.reset_index(drop=True)
    front = ["Timestamp"] + [c for c in wide.columns if c != "Timestamp"]
    return wide[front].copy()
