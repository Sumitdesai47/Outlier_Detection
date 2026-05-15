"""Flask adapter: cluster-consistency actual outliers (build_cluster_consistency_actual_outlier)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_cluster_consistency_actual_outlier import run_cluster_consistency_for_web

from services.auto_without_causal_outlier_drift import (
    _build_plot_inputs,
    _build_reason,
    _format_ts,
    _safe_float,
    _v5_apply_critical_display_filter,
)


def _map_cluster_class_for_plot(fc: str) -> str:
    s = str(fc or "").strip()
    if s == "Normal":
        return "Normal"
    if "Actual Outlier" in s:
        return "Strong Anomaly"
    if "Warning" in s:
        return "Drift"
    if "Cluster Drift" in s:
        return "Contextual Anomaly"
    return "Normal"


def _direction_display(d: Any) -> str:
    u = str(d or "").upper().strip()
    if u == "UP":
        return "High"
    if u == "DOWN":
        return "Low"
    if u == "NORMAL":
        return "Unknown"
    return str(d or "Unknown")


def _parse_peer_tags_cell(cell: Any) -> List[Dict[str, Any]]:
    raw = str(cell or "").strip()
    if not raw:
        return []
    return [
        {"tag": p.strip(), "corr": 1.0}
        for p in raw.split(",")
        if p.strip()
    ]


def run_combined_outlier_drift_ui(
    file_path: str,
    *,
    shutdown_indicator_tags: Optional[Sequence[str]] = None,
    critical_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    shutdown_list = (
        [str(x).strip() for x in shutdown_indicator_tags if x and str(x).strip()]
        if shutdown_indicator_tags
        else None
    )
    bundle = run_cluster_consistency_for_web(
        file_path,
        sheet_name=None,
        timestamp_col="Timestamp",
        shutdown_indicator_tags=shutdown_list,
    )
    combined: pd.DataFrame = bundle["all_df"]
    tag_cols: List[str] = list(bundle["tags"])
    summary_rows: pd.DataFrame = bundle["dashboard"]

    summary: Dict[str, Any] = {
        str(r["Metric"]): r["Value"] for _, r in summary_rows.iterrows()
    }
    summary["Threshold_Mode"] = (
        "Cluster-consistency actual outliers — build_cluster_consistency_actual_outlier.py"
    )

    result_df = combined.copy()
    result_df["Timestamp"] = pd.to_datetime(result_df["Timestamp"], errors="coerce")
    # Part8 timestamp summary expects Abs_Z; cluster uses Target_Z_vs_Clean.
    result_df["Abs_Z"] = pd.to_numeric(
        result_df.get("Target_Z_vs_Clean"), errors="coerce"
    ).abs()
    result_df["Predicted_Value"] = pd.to_numeric(
        result_df.get("Predicted_From_Peers"), errors="coerce"
    )
    result_df["Outlier_Direction"] = result_df["Direction"]
    result_df["Limit_Crossed"] = np.where(
        result_df["Final_Status"].astype(str) == "Normal",
        "Within_Limits",
        "Outer_Range",
    )
    plot_cls = result_df["Final_Class"].map(_map_cluster_class_for_plot)
    result_df["Final_Class"] = plot_cls

    try:
        from services.auto_without_causal_outlier_drift import _load_auto_without_causal_module

        module = _load_auto_without_causal_module()
        timestamp_summary_rows = module.build_timestamp_summary(result_df).to_dict(
            orient="records"
        )
    except Exception:
        timestamp_summary_rows = []

    x_variables_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    for tag in tag_cols:
        sub = combined[combined["Tag"].astype(str) == str(tag)]
        if sub.empty:
            continue
        x_variables_by_tag[str(tag)] = _parse_peer_tags_cell(
            sub.iloc[0].get("Peer_Tags")
        )

    tag_limits_by_tag: Dict[str, Dict[str, Any]] = {}
    for tag in tag_cols:
        sub = combined[combined["Tag"].astype(str) == str(tag)]
        if sub.empty:
            continue
        r0 = sub.iloc[0]
        lo = _safe_float(r0.get("Clean_Outer_Low_0_5pct"))
        hi = _safe_float(r0.get("Clean_Outer_High_99_5pct"))
        ctr = _safe_float(r0.get("Clean_Median"))
        scale = _safe_float(r0.get("Clean_MAD_Scale"))
        if scale is None or scale < 1e-9:
            scale = 1e-9
        tag_limits_by_tag[str(tag)] = {
            "baseline_center": ctr,
            "baseline_scale": scale,
            "drift_lower_limit": lo,
            "drift_upper_limit": hi,
            "drift_anomaly_lower_limit": lo,
            "drift_anomaly_upper_limit": hi,
            "strong_anomaly_lower_limit": lo,
            "strong_anomaly_upper_limit": hi,
        }

    non_normal = combined[combined["Final_Status"].astype(str) != "Normal"].copy()
    tag_summaries: List[Dict[str, Any]] = []
    details_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    monthly_pages_by_tag: Dict[str, List[Dict[str, Any]]] = {}

    if not non_normal.empty:
        for tag, tag_rows in non_normal.groupby("Tag", dropna=True):
            tag_rows = tag_rows.sort_values("Timestamp")
            first = tag_rows.iloc[0]
            plot_status = _map_cluster_class_for_plot(str(first.get("Final_Class") or ""))
            tag_summaries.append(
                {
                    "tag": str(tag),
                    "status": plot_status,
                    "drift_timestamp": _format_ts(first.get("Timestamp")),
                    "num_drift_points": int(len(tag_rows)),
                }
            )

            all_rows = result_df[result_df["Tag"].astype(str) == str(tag)].copy()
            all_rows = all_rows.sort_values("Timestamp", ascending=False)
            details_by_tag[str(tag)] = [
                {
                    "Timestamp": _format_ts(r.get("Timestamp")),
                    "Actual_Value": _safe_float(r.get("Actual_Value")),
                    "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                    "Final_Class": r.get("Final_Class"),
                    "Direction": _direction_display(r.get("Direction")),
                    "Reason": str(r.get("Explanation") or "").strip()
                    or _build_reason(
                        r.get("Final_Class"),
                        _direction_display(r.get("Direction")),
                        None,
                    ),
                }
                for _, r in all_rows.iterrows()
            ]

            tmp = result_df[result_df["Tag"].astype(str) == str(tag)].copy()
            tmp["month_key"] = (
                pd.to_datetime(tmp["Timestamp"], errors="coerce")
                .dt.to_period("M")
                .astype(str)
            )
            pages: List[Dict[str, Any]] = []
            for m in sorted(
                [x for x in tmp["month_key"].dropna().unique().tolist() if x and x != "NaT"],
                reverse=True,
            ):
                month_rows = tmp[tmp["month_key"] == m].copy()
                month_rows = month_rows.sort_values("Timestamp", ascending=False)
                pages.append(
                    {
                        "month": m,
                        "rows": [
                            {
                                "Timestamp": _format_ts(r.get("Timestamp")),
                                "Actual_Value": _safe_float(r.get("Actual_Value")),
                                "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                                "Final_Class": r.get("Final_Class"),
                                "Direction": _direction_display(r.get("Direction")),
                                "Reason": str(r.get("Explanation") or ""),
                            }
                            for _, r in month_rows.iterrows()
                        ],
                    }
                )
            monthly_pages_by_tag[str(tag)] = pages

    top_tags_by_points = sorted(
        tag_summaries,
        key=lambda r: int(r.get("num_drift_points") or 0),
        reverse=True,
    )

    wide_plot, out_df = _build_plot_inputs(result_df)

    out: Dict[str, Any] = {
        "summary": summary,
        "top_tags_by_points": top_tags_by_points[:10],
        "tag_summaries": tag_summaries,
        "details_by_tag": details_by_tag,
        "monthly_pages_by_tag": monthly_pages_by_tag,
        "df_for_script": wide_plot,
        "out_df": out_df,
        "timestamp_summary_rows": timestamp_summary_rows,
        "tag_limits_by_tag": tag_limits_by_tag,
        "x_variables_by_tag": x_variables_by_tag,
    }
    _v5_apply_critical_display_filter(
        out, tag_cols=tag_cols, critical_tags=critical_tags
    )
    if critical_tags:
        crit_set = {
            str(t).strip()
            for t in critical_tags
            if t is not None and str(t).strip()
        } & set(tag_cols)
        if crit_set:
            out["summary"]["Critical_Tags_Display_Only"] = ", ".join(sorted(crit_set))
    if shutdown_list:
        out["summary"]["Shutdown_Filter_Tags"] = ", ".join(
            sorted(set(shutdown_list) & set(tag_cols))
        )
    return out
