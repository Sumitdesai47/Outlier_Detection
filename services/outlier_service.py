from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .time_series_utils import format_date_us_mdy, load_wide_time_series_xlsx, safe_parse_datetime_series


def _robust_z_scores(series: pd.Series) -> pd.Series:
    """
    Robust z-score using median and MAD:
      z = (x - median) / (1.4826 * MAD)
    """
    s = series.dropna().astype(float)
    if len(s) < 5:
        return pd.Series(np.zeros(len(series)), index=series.index)

    median = s.median()
    mad = np.median(np.abs(s - median))
    if mad == 0 or np.isnan(mad):
        return pd.Series(np.zeros(len(series)), index=series.index)

    denom = 1.4826 * mad
    return (series - median) / (denom + 1e-12)


def detect_outliers_in_wide_xlsx(
    file_path: str,
    *,
    sheet_name=0,
    robust_z_threshold: float = 3.5,
    plot_tag: Optional[str] = None,
    timestamp_base_datetime: Optional[str] = None,
    timestamp_unit: str = "D",
) -> dict:
    """
    Scan every tag column (all columns except Timestamp) and flag outliers using robust z-score.

    Returns:
      - flags_df: all flagged rows with tag/timestamp/value/robust_z
      - outlier_status_df: per-tag status (is_outlier, first_timestamp, num_flags)
      - plot_tag_used: tag used for plotting
      - plot_html: plotly HTML for a time-series of the selected tag
    """
    df = load_wide_time_series_xlsx(
        file_path,
        sheet_name=sheet_name,
        timestamp_col_name="Timestamp",
        timestamp_base_datetime=timestamp_base_datetime,
        timestamp_unit=timestamp_unit,
    )
    # Tag columns are everything except the parsed and raw timestamp columns.
    tags = [c for c in df.columns if c not in {"Timestamp", "Timestamp_raw"}]
    if not tags:
        raise ValueError("No tag columns found in XLSX.")

    rows = []
    for tag in tags:
        robust_z = _robust_z_scores(df[tag])
        flags = robust_z.abs() > robust_z_threshold
        if not flags.any():
            continue

        # Use parsed timestamp for UI (US-style M/D/YYYY after formatting).
        if df["Timestamp"].notna().any():
            flagged = df.loc[flags, ["Timestamp", tag]].copy()
        else:
            flagged = df.loc[flags, ["Timestamp_raw", tag]].copy()
            flagged = flagged.rename(columns={"Timestamp_raw": "Timestamp"})

        flagged["robust_z"] = robust_z.loc[flags].values
        flagged["tag"] = tag
        flagged = flagged.rename(columns={"Timestamp": "timestamp", tag: "value"})
        rows.append(flagged)

    flags_df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["tag", "timestamp", "value", "robust_z"]
    )
    # US-style dates for UI: 5/3/2022
    if len(flags_df) > 0 and "timestamp" in flags_df.columns:
        flags_df["timestamp_dt"] = safe_parse_datetime_series(flags_df["timestamp"])
        flags_df = flags_df.sort_values(["tag", "timestamp_dt"]).reset_index(drop=True)
        flags_df["timestamp"] = flags_df["timestamp_dt"].apply(format_date_us_mdy)
        flags_df = flags_df.drop(columns=["timestamp_dt"], errors="ignore")
    else:
        flags_df = flags_df.sort_values(["tag", "timestamp"]).reset_index(drop=True)

    # Choose plot tag:
    if plot_tag is None:
        plot_tag_used = flags_df["tag"].iloc[0] if len(flags_df) > 0 else tags[0]
    else:
        plot_tag_used = plot_tag if plot_tag in df.columns else tags[0]

    # Per-tag status summary (used by UI)
    if len(flags_df) > 0:
        status_rows = []
        for tag in tags:
            tag_flags = flags_df[flags_df["tag"] == tag]
            if len(tag_flags) == 0:
                status_rows.append({"tag": tag, "is_outlier": False, "first_outlier_timestamp": None, "num_flags": 0})
            else:
                first_ts = tag_flags.iloc[0]["timestamp"]
                status_rows.append({"tag": tag, "is_outlier": True, "first_outlier_timestamp": first_ts, "num_flags": int(len(tag_flags))})
        outlier_status_df = pd.DataFrame(status_rows).sort_values("num_flags", ascending=False).reset_index(drop=True)
    else:
        outlier_status_df = pd.DataFrame([{"tag": t, "is_outlier": False, "first_outlier_timestamp": None, "num_flags": 0} for t in tags])

    # Build plot using parsed timestamps, but normalize to day precision
    # so UI shows only YYYY-MM-DD (no time).
    x = df["Timestamp"]
    if pd.api.types.is_datetime64_any_dtype(x):
        x = x.dt.floor("D")

    y = df[plot_tag_used]
    robust_z = _robust_z_scores(y)
    flagged_mask = robust_z.abs() > robust_z_threshold

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
                    x=x,
                    y=y,
            mode="lines",
            name=f"{plot_tag_used} value",
        )
    )
    if flagged_mask.any():
        fig.add_trace(
            go.Scatter(
                x=x.loc[flagged_mask],
                y=y.loc[flagged_mask],
                mode="markers",
                name="Outliers",
                marker=dict(color="red", size=8),
            )
        )

    fig.update_layout(
        title=f"Outlier detection: {plot_tag_used}",
        xaxis_title="Timestamp",
        yaxis_title="Value",
        template="plotly_white",
        legend=dict(orientation="h"),
        height=420,
    )
    fig.update_xaxes(tickformat="%m/%d/%Y", hoverformat="%m/%d/%Y")
    plot_html = fig.to_html(full_html=False, include_plotlyjs=False)

    plot_tag_row = outlier_status_df[outlier_status_df["tag"] == plot_tag_used]
    plot_tag_is_outlier = bool(plot_tag_row["is_outlier"].iloc[0]) if len(plot_tag_row) else False
    plot_tag_first_outlier_timestamp = (
        None if len(plot_tag_row) == 0 else plot_tag_row["first_outlier_timestamp"].iloc[0]
    )
    plot_tag_num_flags = (
        0 if len(plot_tag_row) == 0 else int(plot_tag_row["num_flags"].iloc[0])
    )

    return {
        "flags_df": flags_df,
        "outlier_status_df": outlier_status_df,
        "plot_tag_used": plot_tag_used,
        "plot_html": plot_html,
        "robust_z_threshold": robust_z_threshold,
        "plot_tag_is_outlier": plot_tag_is_outlier,
        "plot_tag_first_outlier_timestamp": plot_tag_first_outlier_timestamp,
        "plot_tag_num_flags": plot_tag_num_flags,
    }

