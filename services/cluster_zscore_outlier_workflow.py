"""Flask adapter: strict PCA cluster true outliers."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build_cluster_pca_strict_outlier as cz

from services.auto_without_causal_outlier_drift import (
    _build_plot_inputs,
    _build_reason,
    _format_ts,
    _safe_float,
    _v5_apply_critical_display_filter,
    clip_plot_inputs_to_wide_timestamps,
    wide_rows_plant_indicator_off,
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


def _apply_shutdown_filter(
    df: pd.DataFrame, ts: str, tag_cols: List[str], shutdown_indicator_tags: Optional[Sequence[str]]
) -> tuple[pd.DataFrame, List[str]]:
    if not shutdown_indicator_tags:
        return df, tag_cols
    shutdown_set = {str(t).strip() for t in shutdown_indicator_tags if t and str(t).strip()}
    shutdown_set = {t for t in shutdown_set if t in df.columns}
    if not shutdown_set:
        return df, tag_cols
    is_shut = wide_rows_plant_indicator_off(df, sorted(shutdown_set))
    df = df.loc[~is_shut].reset_index(drop=True)
    keep = [ts] + [c for c in tag_cols if c in df.columns]
    return df[[c for c in keep if c in df.columns]], tag_cols


def run_cluster_zscore_outlier_ui(
    file_path: str,
    *,
    shutdown_indicator_tags: Optional[Sequence[str]] = None,
    critical_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    cfg = cz.CONFIG.copy()
    cfg["data_file"] = file_path

    # Use the same tolerant ingestion path as part8/part9 (sheet scoring + timestamp auto-detect).
    from services.auto_without_causal_outlier_drift import _load_auto_without_causal_module

    mod = _load_auto_without_causal_module()
    raw_df, _selected_sheet = mod.read_input_file(
        file_path, sheet_name=None, max_rows=None, datetime_format=None
    )
    if raw_df.empty:
        raise ValueError("Selected sheet is empty.")
    ts_detected = mod.detect_timestamp_col(raw_df, override=None, datetime_format=None)
    tag_cols_arg = mod.parse_tag_cols_argument(None)
    long_df, _input_fmt, _dts, _dtag, _dval = mod.make_long_format(
        raw_df,
        timestamp_col=ts_detected,
        tag_col=None,
        value_col=None,
        tag_cols=tag_cols_arg,
        datetime_format=None,
    )
    pivot = mod.build_pivot(long_df)
    if pivot.shape[1] == 0:
        raise ValueError("No tag columns after pivot; check input format.")
    df = pivot.reset_index().copy()
    ts = "Timestamp"
    df[ts] = pd.to_datetime(df[ts], errors="coerce")
    df = df.dropna(subset=[ts]).sort_values(ts).reset_index(drop=True)
    tag_cols = [c for c in df.columns if c != ts]
    for c in tag_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    tag_cols = [c for c in tag_cols if df[c].notna().sum() >= 5]
    if len(tag_cols) < 2:
        raise ValueError("At least two numeric tags are required.")
    df = df[[ts] + tag_cols].copy()

    df, tag_cols = _apply_shutdown_filter(df, ts, tag_cols, shutdown_indicator_tags)
    if len(tag_cols) < 2:
        raise ValueError("At least two numeric tags are required after shutdown filtering.")

    clean_daily, clean_candidates, clean_mask = cz.detect_clean_period(df, ts, tag_cols, cfg)
    ref = cz.build_reference_profile(df, tag_cols, clean_mask, cfg)
    cluster_df, cluster_map, corr_pairs, clean_corr = cz.build_clusters(df, tag_cols, clean_mask, cfg)
    _ = corr_pairs
    results = cz.run_model(df, ts, tag_cols, clean_mask, ref, cluster_df, cluster_map, clean_corr, cfg)

    all_results = results["All_Results"].copy()
    all_results["Timestamp"] = pd.to_datetime(all_results["Timestamp"], errors="coerce")
    all_results["Abs_Z"] = pd.to_numeric(all_results.get("Tag_Abs_Z"), errors="coerce").abs()
    all_results["Predicted_Value"] = pd.to_numeric(
        all_results.get("Expected_Tag_Z_From_Cluster"), errors="coerce"
    )
    all_results["Direction"] = np.where(
        pd.to_numeric(all_results["Tag_Z"], errors="coerce") >= 0, "High", "Low"
    )
    all_results["Limit_Crossed"] = np.where(
        all_results["Final_Status"].astype(str) == "Normal", "Within_Limits", "Outer_Range"
    )
    # Display: only treat Actual Outlier as Strong Anomaly when the value is outside the
    # strong-anomaly band (same mean ± tag_z_strong_limit * Clean_Std as tag limits). Rows
    # that are model outliers but still inside that band show as Drift.
    ref_by_tag = ref.set_index("Tag") if not ref.empty else pd.DataFrame()
    tags_str = all_results["Tag"].astype(str)
    mean_v = tags_str.map(ref_by_tag["Clean_Mean"]) if "Clean_Mean" in ref_by_tag.columns else pd.Series(np.nan, index=all_results.index)
    std_v = tags_str.map(ref_by_tag["Clean_Std"]) if "Clean_Std" in ref_by_tag.columns else pd.Series(np.nan, index=all_results.index)
    std_v = pd.to_numeric(std_v, errors="coerce").replace(0, np.nan).clip(lower=1e-9)
    lim_z = float(cfg["tag_z_strong_limit"])
    lo_s = mean_v - lim_z * std_v
    hi_s = mean_v + lim_z * std_v
    av = pd.to_numeric(all_results["Actual_Value"], errors="coerce")
    can_band = mean_v.notna() & std_v.notna() & av.notna() & lo_s.notna() & hi_s.notna()
    inside_strong_band = can_band & (av >= lo_s) & (av <= hi_s)
    tag_z_abs = pd.to_numeric(all_results["Tag_Z"], errors="coerce").abs()
    use_strong_anomaly_display = (can_band & ~inside_strong_band) | (~can_band & tag_z_abs.ge(lim_z))
    mask_actual_outlier = all_results["Final_Status"].astype(str).to_numpy() == "Actual Outlier"
    base_plot = all_results["Final_Class"].map(_map_cluster_class_for_plot).to_numpy(dtype=object)
    ao_plot = np.where(use_strong_anomaly_display.to_numpy(), "Strong Anomaly", "Drift")
    all_results["Final_Class"] = np.where(mask_actual_outlier, ao_plot, base_plot)

    summary_info = {
        "clean_start": clean_candidates.iloc[0]["Start_Timestamp"],
        "clean_end": clean_candidates.iloc[0]["End_Timestamp"],
        "total_rows": len(df),
        "total_tags": len(tag_cols),
        "total_checks": len(results["All_Results"]),
        "actual_outlier_rows": int((results["All_Results"]["Final_Status"] == "Actual Outlier").sum()),
        "warning_rows": int((results["All_Results"]["Final_Status"] == "Warning").sum()),
        "cluster_drift_rows": int((results["All_Results"]["Final_Status"] == "Cluster Drift").sum()),
        "normal_rows": int((results["All_Results"]["Final_Status"] == "Normal").sum()),
        "actual_outlier_rate": _safe_float((results["All_Results"]["Final_Status"] == "Actual Outlier").mean()),
    }
    summary_rows = cz.make_dashboard(summary_info, cfg)
    summary: Dict[str, Any] = {str(r["Metric"]): r["Value"] for _, r in summary_rows.iterrows()}
    summary["Threshold_Mode"] = (
        "Strict PCA cluster true outliers — "
        "build_cluster_pca_strict_outlier.py"
    )

    top_peers = results.get("Top_Peer_Tags", pd.DataFrame())
    x_variables_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    if not top_peers.empty:
        for tag, g in top_peers.groupby("Tag", dropna=True):
            peer_csv = str(g.iloc[0].get("Top_Peers") or "").strip()
            x_variables_by_tag[str(tag)] = [
                {"tag": p.strip(), "corr": 1.0}
                for p in peer_csv.split(",")
                if p.strip()
            ]

    ref_idx = ref.set_index("Tag", drop=False) if not ref.empty else pd.DataFrame()
    tag_limits_by_tag: Dict[str, Dict[str, Any]] = {}
    for tag in tag_cols:
        if ref_idx.empty or tag not in ref_idx.index:
            continue
        rr = ref_idx.loc[tag]
        ctr = _safe_float(rr.get("Clean_Mean"))
        scale = _safe_float(rr.get("Clean_Std"))
        if scale is None or scale < 1e-9:
            scale = 1e-9
        tag_limits_by_tag[str(tag)] = {
            "baseline_center": ctr,
            "baseline_scale": scale,
            "drift_lower_limit": ctr - cfg["tag_z_limit"] * scale if ctr is not None else None,
            "drift_upper_limit": ctr + cfg["tag_z_limit"] * scale if ctr is not None else None,
            "drift_anomaly_lower_limit": ctr - cfg["residual_z_limit"] * scale if ctr is not None else None,
            "drift_anomaly_upper_limit": ctr + cfg["residual_z_limit"] * scale if ctr is not None else None,
            "strong_anomaly_lower_limit": ctr - cfg["tag_z_strong_limit"] * scale if ctr is not None else None,
            "strong_anomaly_upper_limit": ctr + cfg["tag_z_strong_limit"] * scale if ctr is not None else None,
        }

    non_normal = results["All_Results"][results["All_Results"]["Final_Status"].astype(str) != "Normal"].copy()
    tag_summaries: List[Dict[str, Any]] = []
    details_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    monthly_pages_by_tag: Dict[str, List[Dict[str, Any]]] = {}

    if not non_normal.empty:
        for tag, tag_rows in non_normal.groupby("Tag", dropna=True):
            tag_rows = tag_rows.sort_values("Timestamp")
            first = tag_rows.iloc[0]
            ts_first = pd.to_datetime(first.get("Timestamp"), errors="coerce")
            disp = all_results[
                (all_results["Tag"].astype(str) == str(tag))
                & (pd.to_datetime(all_results["Timestamp"], errors="coerce") == ts_first)
            ]
            if not disp.empty:
                status = str(disp.iloc[0]["Final_Class"])
            else:
                status = _map_cluster_class_for_plot(str(first.get("Final_Class") or ""))
            tag_summaries.append(
                {
                    "tag": str(tag),
                    "status": status,
                    "drift_timestamp": _format_ts(first.get("Timestamp")),
                    "num_drift_points": int(len(tag_rows)),
                }
            )

            all_rows = all_results[all_results["Tag"].astype(str) == str(tag)].copy()
            all_rows = all_rows.sort_values("Timestamp", ascending=False)
            details_by_tag[str(tag)] = [
                {
                    "Timestamp": _format_ts(r.get("Timestamp")),
                    "Actual_Value": _safe_float(r.get("Actual_Value")),
                    "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                    "Final_Class": r.get("Final_Class"),
                    "Direction": str(r.get("Direction") or "Unknown"),
                    "Reason": str(r.get("Logic_Explanation") or "").strip()
                    or _build_reason(r.get("Final_Class"), r.get("Direction"), None),
                }
                for _, r in all_rows.iterrows()
            ]

            tmp = all_rows.copy()
            tmp["month_key"] = (
                pd.to_datetime(tmp["Timestamp"], errors="coerce").dt.to_period("M").astype(str)
            )
            pages: List[Dict[str, Any]] = []
            for m in sorted(
                [x for x in tmp["month_key"].dropna().unique().tolist() if x and x != "NaT"],
                reverse=True,
            ):
                month_rows = tmp[tmp["month_key"] == m].copy()
                pages.append(
                    {
                        "month": m,
                        "rows": [
                            {
                                "Timestamp": _format_ts(r.get("Timestamp")),
                                "Actual_Value": _safe_float(r.get("Actual_Value")),
                                "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                                "Final_Class": r.get("Final_Class"),
                                "Direction": str(r.get("Direction") or "Unknown"),
                                "Reason": str(r.get("Logic_Explanation") or ""),
                            }
                            for _, r in month_rows.iterrows()
                        ],
                    }
                )
            monthly_pages_by_tag[str(tag)] = pages

    top_tags_by_points = sorted(
        tag_summaries, key=lambda r: int(r.get("num_drift_points") or 0), reverse=True
    )
    wide_plot, out_df = _build_plot_inputs(all_results)
    if shutdown_indicator_tags:
        wide_plot, out_df = clip_plot_inputs_to_wide_timestamps(
            wide_plot, out_df, df, ts_name=ts
        )

    try:
        from services.auto_without_causal_outlier_drift import _load_auto_without_causal_module

        mod = _load_auto_without_causal_module()
        timestamp_summary_rows = mod.build_timestamp_summary(all_results).to_dict(orient="records")
    except Exception:
        timestamp_summary_rows = []

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
    _v5_apply_critical_display_filter(out, tag_cols=tag_cols, critical_tags=critical_tags)
    return out
