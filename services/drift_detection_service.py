from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Dict, List

import pandas as pd
import plotly.graph_objects as go

from .time_series_utils import format_date_us_mdy, load_wide_time_series_xlsx, safe_parse_datetime_series

# Marker colors for Drift_detection.py status labels (non-normal only).
_STATUS_MARKER_COLORS = {
    "strong_outlier": "#d61f26",  # red
    "mild_outlier": "#f59e0b",  # amber
    "sudden_jump": "#ea580c",  # orange
    "flatline": "#9333ea",
    "isolation_outlier": "#0284c7",
}


def _build_full_tag_plot(
    df_for_script: pd.DataFrame,
    tag: str,
    tag_result_rows: pd.DataFrame,
    *,
    drift_time: pd.Timestamp | None,
) -> go.Figure:
    """Full time series as line; non-normal points as colored markers by status."""
    ts = safe_parse_datetime_series(df_for_script["Timestamp"])
    y = pd.to_numeric(df_for_script[tag], errors="coerce")

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=ts,
            y=y,
            mode="lines",
            name=str(tag),
            line=dict(color="#94a3b8", width=1.5),
            connectgaps=False,
        )
    )

    sub = tag_result_rows.copy()
    sub["Timestamp"] = safe_parse_datetime_series(sub["Timestamp"])
    for status, grp in sub.groupby("Status", dropna=False):
        if status in ("normal", "missing") or pd.isna(status):
            continue
        grp = grp.dropna(subset=["Timestamp", "Value"])
        if grp.empty:
            continue
        color = _STATUS_MARKER_COLORS.get(str(status), "#64748b")
        fig.add_trace(
            go.Scatter(
                x=grp["Timestamp"],
                y=grp["Value"],
                mode="markers",
                name=str(status),
                marker=dict(size=9, color=color, line=dict(width=0)),
            )
        )

    if drift_time is not None and pd.notna(drift_time):
        fig.add_vline(x=drift_time, line_width=2, line_dash="dash", line_color="#d61f26")

    fig.update_layout(
        title=f"Drift view (full series): {tag}",
        xaxis_title="Timestamp",
        yaxis_title="Value",
        template="plotly_white",
        height=460,
        margin=dict(l=40, r=20, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    fig.update_xaxes(tickformat="%m/%d/%Y", hoverformat="%m/%d/%Y")
    return fig


def build_plot_figure_for_tag(
    df_for_script: pd.DataFrame,
    out_df: pd.DataFrame,
    tag: str,
) -> go.Figure:
    """Build the Part 3 Plotly figure for one tag (full series + markers)."""
    tag = str(tag)
    if tag not in df_for_script.columns:
        raise ValueError(f"Unknown tag: {tag}")
    all_tag_rows = out_df[out_df["Tag"] == tag].copy().sort_values("Timestamp")
    drift_sub = all_tag_rows[~all_tag_rows["Status"].isin(["normal", "missing"])]
    drift_time = None
    if not drift_sub.empty:
        drift_time = pd.to_datetime(drift_sub.iloc[0]["Timestamp"], errors="coerce", dayfirst=False)
    return _build_full_tag_plot(
        df_for_script,
        tag,
        all_tag_rows,
        drift_time=drift_time if drift_time is not None and pd.notna(drift_time) else None,
    )


def _load_drift_script():
    script_path = Path(__file__).resolve().parents[1] / "Drift_detection.py"
    spec = importlib.util.spec_from_file_location("drift_detection_module", str(script_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load Drift_detection.py from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def run_drift_detection_on_xlsx(file_path: str) -> dict:
    """
    Run user-provided Drift_detection.py on uploaded XLSX and return:
    - per-tag first drift status/timestamp
    - dropdown-friendly records
    - df_for_script + out_df for lazy plot loading (do not embed all plots in HTML)
    """
    # Normalize wide-format input; for Part 3 we must preserve actual uploaded timestamps.
    df = load_wide_time_series_xlsx(
        file_path,
        timestamp_col_name="Timestamp",
        timestamp_base_datetime=None,
        timestamp_unit="D",
    )
    if "Timestamp" not in df.columns:
        raise ValueError("Timestamp column not found after parsing uploaded file.")

    # Keep only data expected by script.
    use_cols = [c for c in df.columns if c not in {"Timestamp_raw"}]
    df_for_script = df[use_cols].copy()

    # Prefer true datetime parsing from uploaded values (avoid forced 2024 base behavior).
    parsed_ts = safe_parse_datetime_series(df_for_script["Timestamp"])
    raw_ts = (
        safe_parse_datetime_series(df.get("Timestamp_raw"))
        if "Timestamp_raw" in df.columns
        else pd.Series(dtype="datetime64[ns]")
    )

    if parsed_ts.notna().sum() == 0 and len(raw_ts) > 0 and raw_ts.notna().sum() > 0:
        df_for_script["Timestamp"] = raw_ts
    elif len(raw_ts) > 0 and raw_ts.notna().sum() > parsed_ts.notna().sum():
        # If raw parsing is richer than parsed timestamps, use raw.
        df_for_script["Timestamp"] = raw_ts
    else:
        df_for_script["Timestamp"] = parsed_ts

    module = _load_drift_script()
    detect_outliers = getattr(module, "detect_outliers")
    out_df = detect_outliers(df_for_script, timestamp_col="Timestamp")
    if out_df.empty:
        return {
            "tag_summaries": [],
            "details_by_tag": {},
            "monthly_pages_by_tag": {},
            "df_for_script": df_for_script,
            "out_df": out_df,
        }

    out_df["Timestamp"] = safe_parse_datetime_series(out_df["Timestamp"])
    drift_df = out_df[~out_df["Status"].isin(["normal", "missing"])].copy()
    if drift_df.empty:
        return {
            "tag_summaries": [],
            "details_by_tag": {},
            "monthly_pages_by_tag": {},
            "df_for_script": df_for_script,
            "out_df": out_df,
        }

    tag_summaries: List[dict] = []
    details_by_tag: Dict[str, List[dict]] = {}
    monthly_pages_by_tag: Dict[str, List[dict]] = {}

    for tag, tag_rows in drift_df.groupby("Tag", dropna=True):
        tag_rows = tag_rows.sort_values("Timestamp")
        first_row = tag_rows.iloc[0]
        drift_time = pd.to_datetime(first_row["Timestamp"], errors="coerce", dayfirst=False)
        drift_status = str(first_row["Status"])

        if tag not in df_for_script.columns:
            continue

        # Full per-tag rows for table + plot markers.
        all_tag_rows = out_df[out_df["Tag"] == tag].copy().sort_values("Timestamp")
        all_tag_rows_fmt = all_tag_rows.copy()
        all_tag_rows_fmt["Timestamp_dt"] = safe_parse_datetime_series(all_tag_rows_fmt["Timestamp"])
        all_tag_rows_fmt = all_tag_rows_fmt.sort_values("Timestamp_dt", ascending=False)
        all_tag_rows_fmt["Timestamp"] = all_tag_rows_fmt["Timestamp_dt"].apply(format_date_us_mdy)
        details_by_tag[str(tag)] = all_tag_rows_fmt[
            ["Timestamp", "Value", "Z_score", "Flatline", "Jump", "Isolation", "Status"]
        ].to_dict(orient="records")

        # One-month-at-a-time pagination buckets (descending by month).
        tag_month_pages: List[dict] = []
        tmp = all_tag_rows_fmt.copy()
        tmp["month_key"] = tmp["Timestamp_dt"].dt.to_period("M").astype(str)
        month_keys = [m for m in tmp["month_key"].dropna().unique().tolist() if m and m != "NaT"]
        for m in sorted(month_keys, reverse=True):
            month_rows = tmp[tmp["month_key"] == m].copy().sort_values("Timestamp_dt", ascending=False)
            rows = month_rows[["Timestamp", "Value", "Z_score", "Flatline", "Jump", "Isolation", "Status"]].to_dict(orient="records")
            tag_month_pages.append({"month": m, "rows": rows})
        monthly_pages_by_tag[str(tag)] = tag_month_pages

        tag_summaries.append(
            {
                "tag": str(tag),
                "status": drift_status,
                "drift_timestamp": format_date_us_mdy(drift_time) if pd.notna(drift_time) else "NA",
                "num_drift_points": int(len(tag_rows)),
            }
        )

    tag_summaries = sorted(tag_summaries, key=lambda x: (x["drift_timestamp"], x["tag"]))
    return {
        "tag_summaries": tag_summaries,
        "details_by_tag": details_by_tag,
        "monthly_pages_by_tag": monthly_pages_by_tag,
        "df_for_script": df_for_script,
        "out_df": out_df,
    }

