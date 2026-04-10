"""Plotly JSON for Part 2 (anomaly) smoothed target series."""
from __future__ import annotations

from typing import Any, Sequence

import pandas as pd
import plotly.graph_objects as go

from .time_series_utils import safe_parse_datetime_series


def _normalize_0_1(s: pd.Series) -> pd.Series:
    """Min-max normalize a numeric series to [0, 1] for same-scale overlays."""
    x = pd.to_numeric(s, errors="coerce")
    v = x.dropna()
    if v.empty:
        return x
    lo = float(v.min())
    hi = float(v.max())
    if hi == lo:
        # Flat signal: keep centered so it remains visible.
        return x.apply(lambda t: 0.5 if pd.notna(t) else pd.NA)
    return (x - lo) / (hi - lo)


def build_part2_target_plot_json(
    smoothed_df: pd.DataFrame,
    timestamp_col: str,
    target_tag: str,
    _drift_time_raw: Any,
    compare_tags: Sequence[str] | None = None,
) -> str:
    ts = safe_parse_datetime_series(smoothed_df[timestamp_col])
    y_raw = pd.to_numeric(smoothed_df[target_tag], errors="coerce")
    y = _normalize_0_1(smoothed_df[target_tag])

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=ts,
            y=y,
            mode="lines",
            name=str(target_tag),
            line=dict(color="#b5171e", width=2.2),
            connectgaps=False,
            yaxis="y",
            customdata=y_raw,
            hovertemplate=(
                "<b>%{fullData.name}</b><br>"
                "Time: %{x|%m/%d/%Y}<br>"
                "Actual: %{customdata:.6f}<br>"
                "Normalized: %{y:.4f}<extra></extra>"
            ),
        )
    )

    compare_colors = ["#2563eb", "#16a34a", "#a855f7", "#ea580c", "#0d9488", "#7c3aed", "#ca8a04"]
    valid_compare = [
        str(c)
        for c in (compare_tags or [])
        if c and str(c) in smoothed_df.columns and str(c) != str(target_tag)
    ]
    for i, ct in enumerate(valid_compare):
        yc_raw = pd.to_numeric(smoothed_df[ct], errors="coerce")
        yc = _normalize_0_1(smoothed_df[ct])
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=yc,
                mode="lines",
                name=str(ct),
                line=dict(color=compare_colors[i % len(compare_colors)], width=1.8),
                connectgaps=False,
                opacity=0.9,
                yaxis="y",
                customdata=yc_raw,
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "Time: %{x|%m/%d/%Y}<br>"
                    "Actual: %{customdata:.6f}<br>"
                    "Normalized: %{y:.4f}<extra></extra>"
                ),
            )
        )

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

    y_left_title = "Normalized value (0-1)"
    fig.update_layout(
        xaxis_title="Time",
        yaxis=dict(title=y_left_title, automargin=True),
        template="plotly_white",
        height=440,
        margin=dict(l=56, r=24, t=24, b=96),
        paper_bgcolor="#f8fafc",
        plot_bgcolor="#ffffff",
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.22,
            x=0,
            xanchor="left",
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#e2e8f0",
            borderwidth=1,
        ),
    )
    fig.update_xaxes(tickformat="%m/%d/%Y", hoverformat="%m/%d/%Y", showgrid=True, gridcolor="#e2e8f0")
    fig.update_yaxes(showgrid=True, gridcolor="#e2e8f0")

    return fig.to_json()
