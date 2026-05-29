from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
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

_COMPARE_LINE_COLORS = ["#2563eb", "#16a34a", "#a855f7", "#ea580c", "#0d9488", "#7c3aed", "#ca8a04"]

_DASH_FONT = "Segoe UI, system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif"


def _short_label(name: str, max_len: int = 36) -> str:
    s = str(name).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _minmax_unit_series(series: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """Per-series min–max scale to [0, 1]; returns (normalized, actual)."""
    actual = pd.to_numeric(series, errors="coerce")
    finite = actual[np.isfinite(actual.to_numpy(dtype=float, copy=False))]
    if finite.size == 0:
        return actual * np.nan, actual
    lo, hi = float(np.min(finite)), float(np.max(finite))
    if lo == hi:
        norm = actual.copy()
        norm.loc[actual.notna()] = 0.5
        return norm, actual
    return (actual - lo) / (hi - lo), actual


def _padded_numeric_range(*series: pd.Series, pad_ratio: float = 0.06) -> Optional[Tuple[float, float]]:
    """Min/max across one or more numeric series with symmetric padding (skips NaN)."""
    chunks = []
    for s in series:
        v = pd.to_numeric(s, errors="coerce").to_numpy(dtype=float, copy=False)
        v = v[np.isfinite(v)]
        if v.size:
            chunks.append(v)
    if not chunks:
        return None
    all_v = np.concatenate(chunks)
    lo, hi = float(np.min(all_v)), float(np.max(all_v))
    if lo == hi:
        span = abs(lo) * 0.05 + 1.0
        return lo - span, hi + span
    span = hi - lo
    pad = pad_ratio * span
    return lo - pad, hi + pad


def _timestamp_range(*series: pd.Series) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
    ends: List[pd.Timestamp] = []
    for s in series:
        t = pd.to_datetime(s, errors="coerce")
        t = t[t.notna()]
        if len(t):
            ends.extend([t.min(), t.max()])
    if not ends:
        return None
    return min(ends), max(ends)


def _build_full_tag_plot(
    df_for_script: pd.DataFrame,
    tag: str,
    tag_result_rows: pd.DataFrame,
    *,
    drift_time: pd.Timestamp | None,
    compare_tags: Optional[Sequence[str]] = None,
    normalize_compare: bool = False,
) -> go.Figure:
    """
    Primary tag + drift markers on the left y-axis.
    Compare tags use a separate right y-axis unless normalize_compare is True,
    in which case all series are min–max scaled to [0, 1] on one axis (hover shows actual values).
    """
    ts = safe_parse_datetime_series(df_for_script["Timestamp"])
    y_primary_actual = pd.to_numeric(df_for_script[tag], errors="coerce")

    raw_compare = [str(c) for c in (compare_tags or []) if c and str(c) != str(tag)]
    valid_compare = [ct for ct in raw_compare if ct in df_for_script.columns]
    use_normalized = bool(normalize_compare and valid_compare)

    hover_line = "%{x|%m/%d/%Y}<br>%{fullData.name}<br>Actual: %{customdata:.4f}<extra></extra>"
    hover_marker = "%{x|%m/%d/%Y}<br>%{fullData.name}<br>Actual: %{customdata:.4f}<extra></extra>"

    primary_norm_lo: Optional[float] = None
    primary_norm_hi: Optional[float] = None
    if use_normalized:
        finite_primary = y_primary_actual[np.isfinite(y_primary_actual.to_numpy(dtype=float, copy=False))]
        if finite_primary.size:
            primary_norm_lo = float(np.min(finite_primary))
            primary_norm_hi = float(np.max(finite_primary))

    def _norm_with_primary_bounds(values: pd.Series) -> pd.Series:
        if primary_norm_lo is None or primary_norm_hi is None:
            return values * np.nan
        if primary_norm_lo == primary_norm_hi:
            out = values.copy()
            out.loc[values.notna()] = 0.5
            return out
        return (values - primary_norm_lo) / (primary_norm_hi - primary_norm_lo)

    fig = go.Figure()
    if use_normalized:
        y_primary, y_primary_actual = _minmax_unit_series(y_primary_actual)
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=y_primary,
                customdata=y_primary_actual,
                mode="lines",
                name=_short_label(tag) + " (primary)",
                line=dict(color="#b5171e", width=2.4),
                connectgaps=False,
                hovertemplate=hover_line,
                yaxis="y",
            )
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=y_primary_actual,
                mode="lines",
                name=_short_label(tag) + " (primary)",
                line=dict(color="#b5171e", width=2.4),
                connectgaps=False,
                yaxis="y",
            )
        )

    for i, ct in enumerate(valid_compare):
        yc_actual = pd.to_numeric(df_for_script[ct], errors="coerce")
        color = _COMPARE_LINE_COLORS[i % len(_COMPARE_LINE_COLORS)]
        if use_normalized:
            yc, yc_actual = _minmax_unit_series(yc_actual)
            fig.add_trace(
                go.Scatter(
                    x=ts,
                    y=yc,
                    customdata=yc_actual,
                    mode="lines",
                    name=_short_label(ct),
                    line=dict(color=color, width=2, dash="solid"),
                    connectgaps=False,
                    opacity=0.92,
                    hovertemplate=hover_line,
                    yaxis="y",
                )
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=ts,
                    y=yc_actual,
                    mode="lines",
                    name=_short_label(ct),
                    line=dict(color=color, width=2, dash="solid"),
                    connectgaps=False,
                    opacity=0.92,
                    yaxis="y2",
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
        marker_actual = pd.to_numeric(grp["Value"], errors="coerce")
        if use_normalized:
            marker_y = _norm_with_primary_bounds(marker_actual)
            fig.add_trace(
                go.Scatter(
                    x=grp["Timestamp"],
                    y=marker_y,
                    customdata=marker_actual,
                    mode="markers",
                    name=str(status),
                    marker=dict(size=8, color=color, line=dict(width=0)),
                    hovertemplate=hover_marker,
                    yaxis="y",
                )
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=grp["Timestamp"],
                    y=grp["Value"],
                    mode="markers",
                    name=str(status),
                    marker=dict(size=8, color=color, line=dict(width=0)),
                    yaxis="y",
                )
            )

    if drift_time is not None and pd.notna(drift_time):
        fig.add_vline(x=drift_time, line_width=2, line_dash="dash", line_color="#b5171e", opacity=0.85)

    # Ranges from actual finite data (or fixed 0–1 when comparing normalized)
    rng_y: Optional[Tuple[float, float]] = None
    rng_y2: Optional[Tuple[float, float]] = None
    if use_normalized:
        rng_y = (-0.02, 1.02)
    else:
        primary_for_range = [y_primary_actual]
        for st, grp in sub.groupby("Status", dropna=False):
            if st in ("normal", "missing") or pd.isna(st):
                continue
            g = grp.dropna(subset=["Value"])
            if not g.empty:
                primary_for_range.append(pd.to_numeric(g["Value"], errors="coerce"))
        rng_y = _padded_numeric_range(*primary_for_range)

        compare_series = [pd.to_numeric(df_for_script[ct], errors="coerce") for ct in valid_compare]
        rng_y2 = _padded_numeric_range(*compare_series) if compare_series else None

    if len(sub):
        tr = _timestamp_range(ts, sub["Timestamp"])
    else:
        tr = _timestamp_range(ts)
    if tr is None:
        tr = _timestamp_range(ts)

    title_main = "Time series"
    title_sub = _short_label(tag, 48)
    if valid_compare:
        title_sub += " · vs " + ", ".join(_short_label(c, 24) for c in valid_compare[:3])
        if len(valid_compare) > 3:
            title_sub += "…"
    if use_normalized:
        title_sub += " · normalized 0–1 (hover shows actual values)"

    layout_kwargs: dict = dict(
        title=dict(
            text=f"<b>{title_main}</b><br><span style='font-size:13px;font-weight:500;color:#64748b'>{title_sub}</span>",
            font=dict(family=_DASH_FONT, size=16, color="#0f172a"),
            x=0.01,
            xanchor="left",
            y=0.97,
            yanchor="top",
        ),
        font=dict(family=_DASH_FONT, size=12, color="#334155"),
        paper_bgcolor="#f8fafc",
        plot_bgcolor="#ffffff",
        height=520,
        margin=dict(l=72, r=72, t=92, b=120),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="white", font_size=12, font_family=_DASH_FONT),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.28,
            xanchor="center",
            x=0.5,
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#e2e8f0",
            borderwidth=1,
            font=dict(size=11),
        ),
        xaxis=dict(
            title=dict(text="Time", font=dict(size=12, color="#64748b")),
            showgrid=True,
            gridcolor="#e2e8f0",
            zeroline=False,
            linecolor="#cbd5e1",
            mirror=True,
            tickformat="%m/%d/%Y",
            hoverformat="%m/%d/%Y",
            automargin=True,
        ),
        yaxis=dict(
            title=dict(
                text=(
                    "Normalized (0–1)"
                    if use_normalized
                    else _short_label(tag, 32) + " (left)"
                ),
                font=dict(size=12, color="#b5171e"),
            ),
            showgrid=True,
            gridcolor="#e2e8f0",
            zeroline=False,
            linecolor="#cbd5e1",
            mirror=True,
            side="left",
            automargin=True,
            color="#b5171e",
        ),
    )

    if valid_compare and not use_normalized:
        layout_kwargs["yaxis2"] = dict(
            title=dict(
                text="Compare tags (right scale)" if len(valid_compare) > 1 else _short_label(valid_compare[0], 28) + " (right)",
                font=dict(size=12, color="#2563eb"),
            ),
            overlaying="y",
            side="right",
            showgrid=False,
            zeroline=False,
            linecolor="#cbd5e1",
            mirror=True,
            automargin=True,
            color="#2563eb",
        )

    if rng_y is not None:
        layout_kwargs["yaxis"] = {**layout_kwargs["yaxis"], "range": list(rng_y)}
    if valid_compare and not use_normalized and rng_y2 is not None:
        layout_kwargs["yaxis2"] = {**layout_kwargs["yaxis2"], "range": list(rng_y2)}

    fig.update_layout(**layout_kwargs)

    if tr is not None:
        fig.update_xaxes(range=[tr[0], tr[1]])

    return fig


def build_plot_figure_for_tag(
    df_for_script: pd.DataFrame,
    out_df: pd.DataFrame,
    tag: str,
    *,
    compare_tags: Optional[Sequence[str]] = None,
    normalize_compare: bool = False,
) -> go.Figure:
    """Build the Part 3 Plotly figure for one primary tag (full series + markers) and optional compare lines."""
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
        compare_tags=compare_tags,
        normalize_compare=normalize_compare,
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
    return run_drift_detection_on_wide_df(df)


def run_drift_detection_on_wide_df(df: pd.DataFrame) -> dict:
    """
    Run Drift_detection.py using an already prepared wide DataFrame with Timestamp + tag columns.
    """
    if "Timestamp" not in df.columns:
        raise ValueError("Timestamp column not found after parsing uploaded file.")

    # Drift Detection tab requirement: remove rows where uploaded tag values are all null.
    # This avoids feeding empty calendar rows to Drift_detection.py.
    value_cols = [c for c in df.columns if c not in {"Timestamp", "Timestamp_raw"}]
    if not value_cols:
        raise ValueError("No tag/value columns found in uploaded file.")
    cleaned_df = df.copy()
    obj_cols = [c for c in value_cols if cleaned_df[c].dtype == "object"]
    if obj_cols:
        cleaned_df[obj_cols] = cleaned_df[obj_cols].replace(r"^\s*$", np.nan, regex=True)
    cleaned_df = cleaned_df.loc[~cleaned_df[value_cols].isna().all(axis=1)].copy()
    if cleaned_df.empty:
        raise ValueError("Uploaded file has no usable rows after removing all-null rows.")
    df = cleaned_df

    # Keep only data expected by script.
    use_cols = [c for c in df.columns if c not in {"Timestamp_raw"}]
    df_for_script = df[use_cols].copy()

    # Prefer true datetime parsing from uploaded values (avoid forced 2024 base behavior).
    parsed_ts = safe_parse_datetime_series(df_for_script["Timestamp"])
    raw_ts = safe_parse_datetime_series(df.get("Timestamp_raw")) if "Timestamp_raw" in df.columns else pd.Series(dtype="datetime64[ns]")

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

