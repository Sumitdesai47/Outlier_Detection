"""Plotly JSON for Part 2 (anomaly) smoothed target series."""
from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go

from .time_series_utils import safe_parse_datetime_series


def build_part2_target_plot_json(
    smoothed_df: pd.DataFrame,
    timestamp_col: str,
    target_tag: str,
    drift_time_raw: Any,
) -> str:
    ts = safe_parse_datetime_series(smoothed_df[timestamp_col])
    y = pd.to_numeric(smoothed_df[target_tag], errors="coerce")

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=ts,
            y=y,
            mode="lines",
            name=str(target_tag),
            line=dict(color="#b5171e", width=2.2),
            connectgaps=False,
        )
    )

    dt = pd.to_datetime(drift_time_raw, errors="coerce") if drift_time_raw is not None else pd.NaT
    if pd.notna(dt):
        fig.add_vline(x=dt, line_width=2, line_dash="dash", line_color="#b5171e", opacity=0.9)

    t_min, t_max = ts.min(), ts.max()
    y_valid = pd.to_numeric(y, errors="coerce").dropna()
    if len(y_valid) > 0:
        lo, hi = float(y_valid.min()), float(y_valid.max())
        if lo == hi:
            pad = abs(lo) * 0.05 + 1.0
            lo, hi = lo - pad, hi + pad
        else:
            p = 0.06 * (hi - lo)
            lo, hi = lo - p, hi + p
        fig.update_yaxes(range=[lo, hi])

    if pd.notna(t_min) and pd.notna(t_max):
        fig.update_xaxes(range=[t_min, t_max])

    fig.update_layout(
        title=dict(
            text=f"<b>Smoothed series</b><br><span style='font-size:13px;color:#64748b'>{target_tag}</span>",
            font=dict(size=15),
            x=0.02,
            xanchor="left",
        ),
        xaxis_title="Time",
        yaxis_title="Value (smoothed)",
        template="plotly_white",
        height=440,
        margin=dict(l=56, r=24, t=72, b=48),
        paper_bgcolor="#f8fafc",
        plot_bgcolor="#ffffff",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.05, x=0),
    )
    fig.update_xaxes(tickformat="%m/%d/%Y", hoverformat="%m/%d/%Y", showgrid=True, gridcolor="#e2e8f0")
    fig.update_yaxes(showgrid=True, gridcolor="#e2e8f0")

    return fig.to_json()
