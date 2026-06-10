"""Build Multimodel-style Plotly figures for Plant Analysis result charts."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import pandas as pd
import plotly.graph_objects as go

from services.drift_detection_service import build_plot_figure_for_tag
from services.plant_analysis_multimodel_runner import final_class_to_plot_status
from services.plant_analysis_results_store import is_abnormal_point, point_matches_tab

# Plant Analysis charts use exactly two marker classes (same colors as multimodel).
_PLANT_MARKER_COLORS = {
    "strong_outlier": "#d61f26",
    "sudden_jump": "#ea580c",
}
_PLANT_MARKER_LABELS = {
    "strong_outlier": "Strong outlier",
    "sudden_jump": "Sudden jump",
}


def _collapse_plot_status(plot_status: str) -> Optional[str]:
    """Map every abnormal marker to strong outlier (red) or sudden jump (orange)."""
    ps = str(plot_status or "").strip().lower()
    if ps in ("", "normal", "missing"):
        return None
    if ps == "sudden_jump":
        return "sudden_jump"
    return "strong_outlier"


def _apply_plant_marker_style(fig: go.Figure) -> go.Figure:
    """Keep only red strong-outlier and orange sudden-jump marker traces."""
    for trace in fig.data:
        mode = str(getattr(trace, "mode", "") or "")
        if "markers" not in mode:
            continue
        key = str(getattr(trace, "name", "") or "")
        if key not in _PLANT_MARKER_LABELS:
            continue
        trace.name = _PLANT_MARKER_LABELS[key]
        color = _PLANT_MARKER_COLORS[key]
        trace.marker = dict(size=9, color=color, line=dict(width=0))
    return fig


def _resolve_plot_status(point: Dict[str, Any]) -> str:
    """
    Resolve marker type from stored multimodel fields only.

    Plant dashboard tabs (Outlier Only / Process / Both) must NOT be used as a
    proxy for Strong vs Mild — that caused swapped markers on older runs.
    """
    plot_status = str(point.get("plot_status") or "").strip()
    if plot_status and plot_status != "normal":
        return plot_status

    final_class = str(point.get("final_class") or "").strip()
    final_status = str(point.get("final_status") or "").strip() or None
    if final_class or final_status:
        return final_class_to_plot_status(
            final_class or "Normal",
            final_status=final_status,
        )

    # Legacy rows without multimodel class — single neutral marker (not mild/strong).
    if str(point.get("status") or "") != "Normal":
        return "flagged_unclassified"

    return "normal"


def points_to_plot_frames(
    points: List[Dict[str, Any]],
    tag: str,
    *,
    tab: Optional[str] = None,
    marker_statuses: Optional[Set[str]] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert stored result points to wide + out_df frames used by multimodel plots."""
    tag = str(tag)
    rows = [p for p in points if str(p.get("tag_name") or "") == tag]
    if not rows:
        raise ValueError(f"No data for tag: {tag}")

    rows.sort(
        key=lambda p: (
            pd.to_datetime(p.get("observed_at"), errors="coerce") or pd.Timestamp.min,
            str(p.get("observed_at") or ""),
        )
    )

    wide = pd.DataFrame(
        {
            "Timestamp": [p.get("observed_at") for p in rows],
            tag: [p.get("tag_value") for p in rows],
        }
    )
    wide["Timestamp"] = pd.to_datetime(wide["Timestamp"], errors="coerce")

    out_rows = []
    for p in rows:
        if tab and not point_matches_tab(p, tab):
            continue
        if tab is None and not is_abnormal_point(p):
            continue
        plot_status = _collapse_plot_status(_resolve_plot_status(p))
        if plot_status is None:
            continue
        if marker_statuses and plot_status not in marker_statuses:
            continue
        out_rows.append(
            {
                "Tag": tag,
                "Timestamp": p.get("observed_at"),
                "Value": p.get("tag_value"),
                "Status": plot_status,
            }
        )

    out_df = pd.DataFrame(out_rows)
    if not out_df.empty:
        out_df["Timestamp"] = pd.to_datetime(out_df["Timestamp"], errors="coerce")
        out_df["Value"] = pd.to_numeric(out_df["Value"], errors="coerce")
    else:
        out_df = pd.DataFrame(columns=["Tag", "Timestamp", "Value", "Status"])
    return wide, out_df


def build_plant_analysis_tag_plot(
    points: List[Dict[str, Any]],
    tag: str,
    *,
    tab: Optional[str] = None,
):
    """Same chart builder as Multimodel Outlier Detection (strong outlier, sudden jump, etc.)."""
    wide, out_df = points_to_plot_frames(points, tag, tab=tab)
    fig = build_plot_figure_for_tag(wide, out_df, tag)
    return _apply_plant_marker_style(fig)
