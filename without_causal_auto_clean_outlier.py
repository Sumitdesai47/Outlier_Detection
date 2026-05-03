"""
WITHOUT-CAUSAL AUTO CLEAN OUTLIER

For workflows where the user does not supply a labeled clean/reference dataset:
  1. Automatically detect one stable rolling window (same scorer as clean-anchored V3).
  2. Build per-tag limits from that window only (no merge of similar historical rows).

Classification and persistence match without_causal_clean_anchored_improved_v3
(generate_without_causal_results, CONFIG thresholds).

CLI:
  python without_causal_auto_clean_outlier.py --data_file data.xlsx
"""

from __future__ import annotations

import argparse
from typing import Any

import numpy as np
import pandas as pd

import without_causal_clean_anchored_improved_v3 as v3

CONFIG = v3.CONFIG.copy()
CONFIG["output_file"] = "without_causal_auto_clean_outlier_result.xlsx"


def build_tag_limits_clean_window_only(
    df: pd.DataFrame, clean_df: pd.DataFrame, tag_cols: list, config: dict
) -> pd.DataFrame:
    """
    Robust limits per tag using only values observed inside the auto-detected clean window.
    """
    rows = []
    for tag in tag_cols:
        full = pd.to_numeric(df[tag], errors="coerce")
        clean = pd.to_numeric(clean_df[tag], errors="coerce").dropna()
        if len(clean) == 0:
            continue

        ref_values = clean.copy()
        ref_median_1, ref_scale_1, _ = v3.robust_center_scale(
            ref_values, config["std_epsilon"]
        )
        if pd.isna(ref_scale_1) or ref_scale_1 < config["std_epsilon"]:
            ref_scale_1 = max(float(ref_values.std() or 0), config["std_epsilon"])
        ref_z_1 = (ref_values - ref_median_1) / ref_scale_1
        core_ref = ref_values[ref_z_1.abs() <= 4.0]
        if len(core_ref) >= max(10, int(0.60 * len(ref_values))):
            ref_values = core_ref

        ref_median, ref_scale, _ = v3.robust_center_scale(
            ref_values, config["std_epsilon"]
        )
        ref_mean = ref_values.mean()
        ref_std = ref_values.std()
        if pd.isna(ref_std) or ref_std < config["std_epsilon"]:
            ref_std = ref_scale

        clean_mean = clean.mean()
        clean_median, clean_scale, _ = v3.robust_center_scale(
            clean, config["std_epsilon"]
        )

        robust_lower = ref_median - config["threshold_k"] * ref_scale
        robust_upper = ref_median + config["threshold_k"] * ref_scale
        p_low = ref_values.quantile(config["percentile_low"])
        p_high = ref_values.quantile(config["percentile_high"])
        lower = min(robust_lower, p_low)
        upper = max(robust_upper, p_high)
        width = upper - lower
        if pd.isna(width) or width <= config["std_epsilon"]:
            width = max(ref_scale, config["std_epsilon"])
        lower = lower - config["limit_margin"] * width
        upper = upper + config["limit_margin"] * width
        severity_scale = max(ref_scale, config["std_epsilon"])

        rows.append(
            {
                "Tag": tag,
                "Reference_Source": "Auto_Detected_Clean_Window_Only",
                "Clean_Count": len(clean),
                "Candidate_Count_Before_Trim": len(clean),
                "Reference_Count_Final": len(ref_values),
                "Candidate_Fraction_Of_All_Rows": v3.safe_divide(
                    len(clean), int(full.notna().sum())
                ),
                "Clean_Mean": clean_mean,
                "Clean_Median": clean_median,
                "Clean_Std": clean.std(),
                "Clean_Robust_Scale": clean_scale,
                "Reference_Mean": ref_mean,
                "Reference_Median": ref_median,
                "Reference_Std": ref_std,
                "Reference_Robust_Scale": ref_scale,
                "Severity_Scale": severity_scale,
                "Candidate_Z_Limit": np.nan,
                "Threshold_K": config["threshold_k"],
                "Limit_Margin": config["limit_margin"],
                "Lower_Limit": lower,
                "Upper_Limit": upper,
                "Initial_Clean_Lower": clean_median
                - config["threshold_k"] * clean_scale,
                "Initial_Clean_Upper": clean_median
                + config["threshold_k"] * clean_scale,
            }
        )

    limits = pd.DataFrame(rows)
    if limits.empty:
        raise ValueError("No reference limits built from clean window.")
    return limits


def create_summary_auto_clean(
    df: pd.DataFrame,
    tag_cols: list,
    clean_info: pd.DataFrame,
    limits_df: pd.DataFrame,
    all_results: pd.DataFrame,
    comparison_outputs: Any,
    config: dict,
) -> pd.DataFrame:
    total = len(all_results)
    abnormal = int((all_results["Final_Status"] == "Abnormal").sum())
    normal = int((all_results["Final_Status"] == "Normal").sum())

    rows = [
        {
            "Metric": "Method",
            "Value": "Without causal - auto clean window limits (no similar-history merge)",
        },
        {
            "Metric": "Improvement",
            "Value": "No user clean labels; stable window is auto-selected; limits use that window only.",
        },
        {
            "Metric": "Contrast vs V3 Identification",
            "Value": "Identification tab adds historical rows similar to clean; this tab does not.",
        },
        {"Metric": "Total Raw Rows", "Value": len(df)},
        {"Metric": "Total Tags", "Value": len(tag_cols)},
        {"Metric": "Total Tag-Timestamp Points", "Value": total},
        {"Metric": "Normal Points", "Value": normal},
        {"Metric": "Abnormal Points", "Value": abnormal},
        {"Metric": "Abnormal Rate", "Value": v3.safe_divide(abnormal, total)},
        {"Metric": "Clean Start", "Value": clean_info.loc[0, "Clean_Start_Time"]},
        {"Metric": "Clean End", "Value": clean_info.loc[0, "Clean_End_Time"]},
        {"Metric": "Clean Rows", "Value": clean_info.loc[0, "Clean_Rows"]},
        {"Metric": "Threshold K", "Value": config["threshold_k"]},
        {"Metric": "Limit Margin", "Value": config["limit_margin"]},
        {"Metric": "Persistence Window", "Value": config["persistence_window"]},
        {
            "Metric": "Persistence Min Points",
            "Value": config["persistence_min_points"],
        },
    ]
    if comparison_outputs is not None:
        for k, v in comparison_outputs["Binary_Summary"].iloc[0].to_dict().items():
            rows.append({"Metric": f"Comparison - {k}", "Value": v})
    return pd.DataFrame(rows)


def main(config: dict) -> dict:
    df = v3.read_excel_file(config["data_file"], config["data_sheet_name"])
    df = v3.clean_column_names(df)

    timestamp_col = config["timestamp_col"]
    if timestamp_col not in df.columns:
        detected = v3.find_column(df, ["Timestamp", "Time", "DateTime", "Date"])
        if detected is None:
            raise ValueError("Timestamp column not found in raw data.")
        timestamp_col = detected

    df[timestamp_col] = pd.to_datetime(df[timestamp_col], errors="coerce")
    df = df.dropna(subset=[timestamp_col]).sort_values(timestamp_col).reset_index(
        drop=True
    )

    tag_cols = [c for c in df.columns if c != timestamp_col]
    numeric_tags = []
    for c in tag_cols:
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().sum() > 0:
            df[c] = s
            numeric_tags.append(c)
    tag_cols = numeric_tags
    if len(tag_cols) == 0:
        raise ValueError("No numeric tag columns found.")

    clean_df, clean_info, clean_score = v3.detect_clean_window(
        df, tag_cols, timestamp_col, config
    )
    limits_df = build_tag_limits_clean_window_only(df, clean_df, tag_cols, config)
    all_results = v3.generate_without_causal_results(
        df, tag_cols, timestamp_col, limits_df, config
    )

    benchmark_df = v3.load_benchmark_all_results(
        config["benchmark_result_file"], config["benchmark_sheet_name"]
    )
    comparison_outputs = None
    if benchmark_df is not None:
        comparison_outputs = v3.compare_results(
            all_results, benchmark_df, timestamp_col
        )

    summary = create_summary_auto_clean(
        df, tag_cols, clean_info, limits_df, all_results, comparison_outputs, config
    )

    sheets = {
        "Summary": summary,
        "Auto_Clean_Anchor": clean_info,
        "Clean_Score_By_Row": clean_score,
        "Reference_Limits": limits_df,
        "Without_Causal_All_Results": all_results,
    }
    if comparison_outputs is not None:
        sheets.update(
            {
                "Binary_Summary": comparison_outputs["Binary_Summary"],
                "Comparison_Row_Tag": comparison_outputs["Comparison_Row_Tag"],
            }
        )
    v3.write_excel(config["output_file"], sheets)
    return {
        "summary": summary,
        "clean_info": clean_info,
        "limits": limits_df,
        "all_results": all_results,
    }


def parse_args():
    p = argparse.ArgumentParser(description="Auto clean window without-causal outlier")
    p.add_argument("--data_file", type=str, default=CONFIG["data_file"])
    p.add_argument("--data_sheet_name", type=str, default=None)
    p.add_argument("--output_file", type=str, default=CONFIG["output_file"])
    p.add_argument("--clean_window_rows", type=int, default=None)
    p.add_argument("--threshold_k", type=float, default=CONFIG["threshold_k"])
    p.add_argument("--limit_margin", type=float, default=CONFIG["limit_margin"])
    p.add_argument("--persistence_window", type=int, default=CONFIG["persistence_window"])
    p.add_argument(
        "--persistence_min_points", type=int, default=CONFIG["persistence_min_points"]
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    cfg = CONFIG.copy()
    cfg["data_file"] = args.data_file
    cfg["data_sheet_name"] = args.data_sheet_name
    cfg["output_file"] = args.output_file
    cfg["clean_window_rows"] = args.clean_window_rows
    cfg["threshold_k"] = args.threshold_k
    cfg["limit_margin"] = args.limit_margin
    cfg["persistence_window"] = args.persistence_window
    cfg["persistence_min_points"] = args.persistence_min_points
    main(cfg)
