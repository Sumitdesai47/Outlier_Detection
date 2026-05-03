"""
Regime-aware WITHOUT-CAUSAL outlier detection.

This version fixes two common issues:
1) One manually selected clean period can be high/low biased for some tags.
2) Full-data limits become too wide because abnormal values are included.

Core idea:
- Do not use one fixed clean window only.
- Learn tag-wise normal operating bands from stable/high-density rows.
- If a later period falls inside the same learned normal band, it is marked Normal.
- Drift is assigned only when values are outside learned normal band and persistent.

Run example:
python without_causal_regime_aware_outlier_v2.py \
  --data_file "Multi_X_Multi_Y_Correct_Data.xlsx" \
  --benchmark_file "context_aware_outlier_results(1).xlsx" \
  --output_file "without_causal_regime_aware_result.xlsx"

Required:
pip install pandas numpy openpyxl
"""

import argparse
import os
import numpy as np
import pandas as pd


DEFAULTS = {
    "data_file": "Multi_X_Multi_Y_Correct_Data.xlsx",
    "benchmark_file": "",
    "output_file": "without_causal_regime_aware_result.xlsx",
    "data_sheet_name": None,
    "benchmark_sheet_name": "All_Results",
    "timestamp_col": "Timestamp",
    "global_z_limit": 3.5,
    "jump_z_limit": 4.0,
    "stable_quantile": 0.45,
    "max_reference_bands": 2,
    "min_band_fraction": 0.06,
    "min_band_rows": 20,
    "gap_multiplier": 4.0,
    "band_k": 3.0,
    "soft_band_expand": 0.10,
    "persistence_window": 3,
    "persistence_min_points": 2,
    "drift_z": 3.0,
    "drift_anomaly_z": 3.5,
    "strong_anomaly_z": 5.0,
    "std_epsilon": 1e-9,
}


def clean_column_names(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def read_excel_file(file_path, sheet_name=None):
    if sheet_name in [None, "", "None"]:
        return pd.read_excel(file_path)
    return pd.read_excel(file_path, sheet_name=sheet_name)


def find_column(df, possible_names):
    lookup = {str(c).strip().lower(): c for c in df.columns}
    for name in possible_names:
        if name.lower() in lookup:
            return lookup[name.lower()]
    for c in df.columns:
        c_norm = str(c).strip().lower().replace(" ", "_")
        for name in possible_names:
            if c_norm == name.lower().replace(" ", "_"):
                return c
    return None


def safe_divide(a, b):
    if b == 0 or pd.isna(b):
        return np.nan
    return a / b


def robust_scale(values, eps=1e-9):
    s = pd.Series(values).dropna().astype(float)
    if len(s) == 0:
        return np.nan, eps
    med = s.median()
    mad = (s - med).abs().median()
    scale = 1.4826 * mad
    if pd.isna(scale) or scale < eps:
        scale = s.std()
    if pd.isna(scale) or scale < eps:
        scale = eps
    return med, scale


def robust_zscore(df_num, eps=1e-9):
    med = df_num.median(axis=0, skipna=True)
    mad = (df_num - med).abs().median(axis=0, skipna=True)
    scale = 1.4826 * mad
    std = df_num.std(axis=0, skipna=True)
    bad = (scale < eps) | scale.isna()
    scale[bad] = std[bad]
    bad = (scale < eps) | scale.isna()
    scale[bad] = 1.0
    return (df_num - med) / scale


def binary_status(final_class):
    if pd.isna(final_class):
        return "Unknown"
    fc = str(final_class).strip().lower()
    if fc in ["normal", "ok", "good"]:
        return "Normal"
    if fc in ["unknown", "", "nan", "none"]:
        return "Unknown"
    return "Abnormal"


def detect_global_stable_rows(df, tag_cols, timestamp_col, cfg):
    """Find stable candidate rows. These are candidates only, not final clean rows."""
    data = df[tag_cols].apply(pd.to_numeric, errors="coerce")
    z_level = robust_zscore(data, cfg["std_epsilon"])
    level_bad_frac = (z_level.abs() > cfg["global_z_limit"]).mean(axis=1)

    diff_data = data.diff().abs().fillna(0)
    z_jump = robust_zscore(diff_data, cfg["std_epsilon"])
    jump_bad_frac = (z_jump.abs() > cfg["jump_z_limit"]).mean(axis=1)

    stable_score = 0.75 * level_bad_frac.fillna(1.0) + 0.25 * jump_bad_frac.fillna(1.0)
    cutoff = stable_score.quantile(cfg["stable_quantile"])
    is_stable = stable_score <= cutoff

    return pd.DataFrame({
        timestamp_col: df[timestamp_col],
        "Row_Level_Bad_Fraction": level_bad_frac,
        "Row_Jump_Bad_Fraction": jump_bad_frac,
        "Stable_Score": stable_score,
        "Stable_Score_Cutoff": cutoff,
        "Is_Global_Stable": is_stable,
    })


def split_values_into_bands(values, cfg):
    """
    1D natural band clustering using sorted-value gaps.
    No sklearn required.
    """
    s = pd.Series(values).dropna().astype(float)
    if len(s) == 0:
        return []
    if len(s) < cfg["min_band_rows"]:
        med, scale = robust_scale(s, cfg["std_epsilon"])
        return [{"values": s, "median": med, "scale": scale, "count": len(s), "fraction": 1.0}]

    arr = np.sort(s.values)
    gaps = np.diff(arr)
    pos_gaps = gaps[gaps > 0]
    if len(pos_gaps) == 0:
        med, scale = robust_scale(s, cfg["std_epsilon"])
        return [{"values": s, "median": med, "scale": scale, "count": len(s), "fraction": 1.0}]

    median_gap = np.median(pos_gaps)
    q75_gap = np.quantile(pos_gaps, 0.75)
    gap_threshold = max(
        median_gap * cfg["gap_multiplier"],
        q75_gap * 2.0,
        cfg["std_epsilon"],
    )

    split_pos = np.where(gaps > gap_threshold)[0] + 1
    clusters = np.split(arr, split_pos)
    bands = []
    total = len(s)

    for cl in clusters:
        if len(cl) == 0:
            continue
        frac = len(cl) / total
        if len(cl) < cfg["min_band_rows"] and frac < cfg["min_band_fraction"]:
            continue
        cl_s = pd.Series(cl)
        med, scale = robust_scale(cl_s, cfg["std_epsilon"])
        bands.append({"values": cl_s, "median": med, "scale": scale, "count": len(cl), "fraction": frac})

    if not bands:
        med, scale = robust_scale(s, cfg["std_epsilon"])
        bands = [{"values": s, "median": med, "scale": scale, "count": len(s), "fraction": 1.0}]

    return bands


def learn_reference_bands(df, tag_cols, timestamp_col, stable_rows, cfg):
    """
    Learn normal value bands per tag from stable rows.
    Select largest recurring stable bands to avoid small abnormal pockets becoming reference.
    """
    is_stable = stable_rows["Is_Global_Stable"].values
    rows = []

    for tag in tag_cols:
        full = pd.to_numeric(df[tag], errors="coerce")
        stable_vals = full[is_stable].dropna()

        # fallback: trimmed full data, but not extreme tails
        if len(stable_vals) < cfg["min_band_rows"]:
            q01, q99 = full.quantile(0.01), full.quantile(0.99)
            stable_vals = full[(full >= q01) & (full <= q99)].dropna()

        bands = split_values_into_bands(stable_vals, cfg)
        bands = sorted(bands, key=lambda b: b["count"], reverse=True)
        selected = bands[: cfg["max_reference_bands"]]

        for band_id, b in enumerate(selected, start=1):
            vals = b["values"]
            med = b["median"]
            scale = b["scale"]
            q01 = vals.quantile(0.01)
            q05 = vals.quantile(0.05)
            q95 = vals.quantile(0.95)
            q99 = vals.quantile(0.99)

            robust_lower = med - cfg["band_k"] * scale
            robust_upper = med + cfg["band_k"] * scale

            # Hybrid: robust limit plus percentile guard
            lower = min(robust_lower, q01)
            upper = max(robust_upper, q99)

            width = upper - lower
            if pd.isna(width) or width <= cfg["std_epsilon"]:
                width = max(scale, cfg["std_epsilon"])
            lower = lower - cfg["soft_band_expand"] * width
            upper = upper + cfg["soft_band_expand"] * width

            rows.append({
                "Tag": tag,
                "Band_ID": band_id,
                "Band_Count": b["count"],
                "Band_Fraction_Within_Stable": b.get("fraction", np.nan),
                "Band_Median": med,
                "Band_Scale": scale,
                "Band_Q01": q01,
                "Band_Q05": q05,
                "Band_Q95": q95,
                "Band_Q99": q99,
                "Lower_Limit": lower,
                "Upper_Limit": upper,
                "Band_Method": "Stable-row density band + robust MAD/percentile limits",
            })

    bands_df = pd.DataFrame(rows)
    if bands_df.empty:
        raise ValueError("No reference bands learned. Check data quality or lower min_band_rows.")
    return bands_df


def score_value_against_bands(value, tag_bands):
    if pd.isna(value):
        return False, np.nan, np.nan, np.nan, np.nan

    best_abs = np.inf
    best_score = np.nan
    best_band = np.nan
    best_lower = np.nan
    best_upper = np.nan
    inside_any = False

    for _, b in tag_bands.iterrows():
        lower = b["Lower_Limit"]
        upper = b["Upper_Limit"]
        scale = b["Band_Scale"]
        if pd.isna(scale) or scale <= 0:
            scale = 1e-9

        if lower <= value <= upper:
            score = 0.0
            abs_score = 0.0
            inside_any = True
        elif value < lower:
            score = (value - lower) / scale
            abs_score = abs(score)
        else:
            score = (value - upper) / scale
            abs_score = abs(score)

        if abs_score < best_abs:
            best_abs = abs_score
            best_score = score
            best_band = b["Band_ID"]
            best_lower = lower
            best_upper = upper

    return inside_any, best_band, best_score, best_lower, best_upper


def classify_point(score, effective_outside, persistent, cfg):
    if not effective_outside:
        return "Normal"
    if pd.isna(score):
        return "Unknown"

    abs_score = abs(score)

    if abs_score >= cfg["strong_anomaly_z"]:
        return "Strong Anomaly"

    # Weak outside points are ignored unless persistent
    if not persistent:
        return "Normal"

    if abs_score < cfg["drift_z"]:
        return "Normal"
    if abs_score < cfg["drift_anomaly_z"]:
        return "Drift"
    if abs_score < cfg["strong_anomaly_z"]:
        return "Drift + Anomaly"
    return "Strong Anomaly"


def generate_all_results(df, tag_cols, timestamp_col, bands_df, cfg):
    parts = []

    for tag in tag_cols:
        tag_bands = bands_df[bands_df["Tag"] == tag].copy()
        if tag_bands.empty:
            continue

        temp = df[[timestamp_col, tag]].copy()
        temp.rename(columns={tag: "Actual_Value"}, inplace=True)
        temp["Tag"] = tag
        temp["Actual_Value"] = pd.to_numeric(temp["Actual_Value"], errors="coerce")

        scored = temp["Actual_Value"].apply(lambda v: score_value_against_bands(v, tag_bands))
        temp["Inside_Normal_Band"] = [x[0] for x in scored]
        temp["Nearest_Band_ID"] = [x[1] for x in scored]
        temp["Band_Distance_Z"] = [x[2] for x in scored]
        temp["Lower_Limit"] = [x[3] for x in scored]
        temp["Upper_Limit"] = [x[4] for x in scored]
        temp["Abs_Band_Distance_Z"] = temp["Band_Distance_Z"].abs()
        temp["Outside_Normal_Band"] = ~temp["Inside_Normal_Band"]

        outside_int = temp["Outside_Normal_Band"].astype(int)
        temp["Outside_Count_In_Window"] = outside_int.rolling(
            window=cfg["persistence_window"], min_periods=1, center=True
        ).sum()
        temp["Persistent_Outside"] = temp["Outside_Count_In_Window"] >= cfg["persistence_min_points"]
        temp["Strong_Outside"] = temp["Abs_Band_Distance_Z"] >= cfg["strong_anomaly_z"]
        temp["Effective_Outside"] = temp["Strong_Outside"] | (temp["Outside_Normal_Band"] & temp["Persistent_Outside"])

        temp["Limit_Status"] = np.select(
            [
                temp["Inside_Normal_Band"],
                temp["Actual_Value"] < temp["Lower_Limit"],
                temp["Actual_Value"] > temp["Upper_Limit"],
            ],
            [
                "Within Learned Normal Band",
                "Below Learned Normal Band",
                "Above Learned Normal Band",
            ],
            default="Unknown",
        )

        temp["Direction"] = np.select(
            [
                temp["Inside_Normal_Band"],
                temp["Actual_Value"] < temp["Lower_Limit"],
                temp["Actual_Value"] > temp["Upper_Limit"],
            ],
            ["Normal", "Down", "Up"],
            default="Unknown",
        )

        temp["Final_Class"] = temp.apply(
            lambda r: classify_point(r["Band_Distance_Z"], r["Effective_Outside"], r["Persistent_Outside"], cfg),
            axis=1,
        )
        temp["Final_Status"] = temp["Final_Class"].apply(binary_status)

        # Causal-style blank fields
        temp["Predicted_Value"] = np.nan
        temp["Residual"] = np.nan
        temp["Residual_Z"] = np.nan
        temp["Reference_Mean"] = np.nan
        temp["Reference_Std"] = np.nan
        temp["Method"] = "Without Causal - Regime-Aware Learned Normal Bands"

        temp = temp[[
            timestamp_col,
            "Tag",
            "Actual_Value",
            "Predicted_Value",
            "Residual",
            "Residual_Z",
            "Reference_Mean",
            "Reference_Std",
            "Nearest_Band_ID",
            "Lower_Limit",
            "Upper_Limit",
            "Band_Distance_Z",
            "Abs_Band_Distance_Z",
            "Limit_Status",
            "Direction",
            "Inside_Normal_Band",
            "Outside_Normal_Band",
            "Outside_Count_In_Window",
            "Persistent_Outside",
            "Strong_Outside",
            "Effective_Outside",
            "Final_Class",
            "Final_Status",
            "Method",
        ]]
        parts.append(temp)

    return pd.concat(parts, ignore_index=True)


def create_row_status(all_results, timestamp_col):
    row_status = all_results.groupby(timestamp_col).agg(
        Total_Tags=("Tag", "count"),
        Abnormal_Tag_Count=("Final_Status", lambda x: (x == "Abnormal").sum()),
        Drift_Count=("Final_Class", lambda x: (x == "Drift").sum()),
        Drift_Anomaly_Count=("Final_Class", lambda x: (x == "Drift + Anomaly").sum()),
        Strong_Anomaly_Count=("Final_Class", lambda x: (x == "Strong Anomaly").sum()),
    ).reset_index()
    row_status["Abnormal_Tag_Rate"] = row_status["Abnormal_Tag_Count"] / row_status["Total_Tags"]
    row_status["Row_Final_Status"] = np.where(row_status["Abnormal_Tag_Count"] > 0, "Abnormal", "Normal")
    return row_status


def create_tag_summary(all_results):
    tag_summary = all_results.groupby("Tag").agg(
        Total_Rows=("Final_Status", "count"),
        Normal_Count=("Final_Status", lambda x: (x == "Normal").sum()),
        Abnormal_Count=("Final_Status", lambda x: (x == "Abnormal").sum()),
        Drift_Count=("Final_Class", lambda x: (x == "Drift").sum()),
        Drift_Anomaly_Count=("Final_Class", lambda x: (x == "Drift + Anomaly").sum()),
        Strong_Anomaly_Count=("Final_Class", lambda x: (x == "Strong Anomaly").sum()),
    ).reset_index()
    tag_summary["Abnormal_Rate"] = tag_summary["Abnormal_Count"] / tag_summary["Total_Rows"]
    return tag_summary.sort_values("Abnormal_Rate", ascending=False)


def load_benchmark_all_results(file_path, sheet_name="All_Results"):
    if file_path is None or str(file_path).strip() == "":
        return None
    if not os.path.exists(file_path):
        print(f"Benchmark file not found: {file_path}")
        return None

    xl = pd.ExcelFile(file_path)
    if sheet_name in xl.sheet_names:
        bench = pd.read_excel(file_path, sheet_name=sheet_name)
    else:
        possible = None
        for s in xl.sheet_names:
            if "all" in s.lower() and "result" in s.lower():
                possible = s
                break
        if possible is None:
            print("Benchmark All_Results sheet not found. Skipping comparison.")
            return None
        bench = pd.read_excel(file_path, sheet_name=possible)
    return clean_column_names(bench)


def standardize_benchmark_columns(bench_df):
    timestamp_col = find_column(bench_df, ["Timestamp", "Time", "DateTime", "Date"])
    tag_col = find_column(bench_df, ["Tag", "Target_Tag", "Variable", "Column", "Parameter"])
    class_col = find_column(bench_df, ["Final_Class", "Final Class", "Class", "Status"])
    status_col = find_column(bench_df, ["Final_Status", "Final Status", "Binary_Status"])

    if timestamp_col is None:
        raise ValueError("Timestamp column not found in benchmark file.")
    if tag_col is None:
        raise ValueError("Tag column not found in benchmark file.")
    if class_col is None:
        raise ValueError("Final_Class/Class/Status column not found in benchmark file.")

    out = bench_df[[timestamp_col, tag_col, class_col]].copy()
    out.columns = ["Timestamp", "Tag", "Benchmark_Final_Class"]
    if status_col is not None:
        out["Benchmark_Final_Status"] = bench_df[status_col].apply(binary_status)
    else:
        out["Benchmark_Final_Status"] = out["Benchmark_Final_Class"].apply(binary_status)

    out["Timestamp"] = pd.to_datetime(out["Timestamp"], errors="coerce")
    out["Tag"] = out["Tag"].astype(str).str.strip()
    return out.dropna(subset=["Timestamp", "Tag"])


def compare_results(all_results, benchmark_df, timestamp_col):
    wc = all_results.copy().rename(columns={timestamp_col: "Timestamp"})
    wc["Timestamp"] = pd.to_datetime(wc["Timestamp"], errors="coerce")
    wc["Tag"] = wc["Tag"].astype(str).str.strip()

    wc_small = wc[[
        "Timestamp",
        "Tag",
        "Actual_Value",
        "Nearest_Band_ID",
        "Lower_Limit",
        "Upper_Limit",
        "Band_Distance_Z",
        "Limit_Status",
        "Direction",
        "Final_Class",
        "Final_Status",
    ]].copy()
    wc_small.rename(columns={
        "Final_Class": "Without_Causal_Final_Class",
        "Final_Status": "Without_Causal_Final_Status",
    }, inplace=True)

    bench = standardize_benchmark_columns(benchmark_df)
    comp = wc_small.merge(bench, on=["Timestamp", "Tag"], how="inner")

    comp["Class_Match"] = comp["Without_Causal_Final_Class"].astype(str).str.lower() == comp["Benchmark_Final_Class"].astype(str).str.lower()
    comp["Binary_Match"] = comp["Without_Causal_Final_Status"].astype(str).str.lower() == comp["Benchmark_Final_Status"].astype(str).str.lower()

    comp["Comparison_Result"] = np.select(
        [
            (comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Without_Causal_Final_Status"] == "Abnormal"),
            (comp["Benchmark_Final_Status"] == "Normal") & (comp["Without_Causal_Final_Status"] == "Normal"),
            (comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Without_Causal_Final_Status"] == "Normal"),
            (comp["Benchmark_Final_Status"] == "Normal") & (comp["Without_Causal_Final_Status"] == "Abnormal"),
        ],
        ["Both Abnormal", "Both Normal", "Benchmark Only Abnormal", "Without Causal Only Abnormal"],
        default="Other",
    )

    tp = ((comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Without_Causal_Final_Status"] == "Abnormal")).sum()
    tn = ((comp["Benchmark_Final_Status"] == "Normal") & (comp["Without_Causal_Final_Status"] == "Normal")).sum()
    fp = ((comp["Benchmark_Final_Status"] == "Normal") & (comp["Without_Causal_Final_Status"] == "Abnormal")).sum()
    fn = ((comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Without_Causal_Final_Status"] == "Normal")).sum()
    total = len(comp)

    binary_summary = pd.DataFrame([{
        "Total_Matched_Rows": total,
        "TP_Both_Abnormal": tp,
        "TN_Both_Normal": tn,
        "FP_Without_Causal_Only": fp,
        "FN_Benchmark_Only": fn,
        "Benchmark_Abnormal_Rows": int((comp["Benchmark_Final_Status"] == "Abnormal").sum()),
        "Without_Causal_Abnormal_Rows": int((comp["Without_Causal_Final_Status"] == "Abnormal").sum()),
        "Binary_Agreement_Accuracy": safe_divide(tp + tn, total),
        "Precision_vs_Benchmark": safe_divide(tp, tp + fp),
        "Recall_vs_Benchmark": safe_divide(tp, tp + fn),
        "Specificity_vs_Benchmark": safe_divide(tn, tn + fp),
        "Exact_Final_Class_Match": comp["Class_Match"].mean() if total else np.nan,
    }])

    class_comparison = pd.crosstab(comp["Benchmark_Final_Class"], comp["Without_Causal_Final_Class"], margins=True).reset_index()
    binary_comparison = pd.crosstab(comp["Benchmark_Final_Status"], comp["Without_Causal_Final_Status"], margins=True).reset_index()

    tag_rows = []
    for tag, g in comp.groupby("Tag"):
        tp_t = ((g["Benchmark_Final_Status"] == "Abnormal") & (g["Without_Causal_Final_Status"] == "Abnormal")).sum()
        tn_t = ((g["Benchmark_Final_Status"] == "Normal") & (g["Without_Causal_Final_Status"] == "Normal")).sum()
        fp_t = ((g["Benchmark_Final_Status"] == "Normal") & (g["Without_Causal_Final_Status"] == "Abnormal")).sum()
        fn_t = ((g["Benchmark_Final_Status"] == "Abnormal") & (g["Without_Causal_Final_Status"] == "Normal")).sum()
        tag_rows.append({
            "Tag": tag,
            "Total_Rows": len(g),
            "TP_Both_Abnormal": tp_t,
            "TN_Both_Normal": tn_t,
            "FP_Without_Causal_Only": fp_t,
            "FN_Benchmark_Only": fn_t,
            "Benchmark_Abnormal": int((g["Benchmark_Final_Status"] == "Abnormal").sum()),
            "Without_Causal_Abnormal": int((g["Without_Causal_Final_Status"] == "Abnormal").sum()),
            "Accuracy": safe_divide(tp_t + tn_t, len(g)),
            "Precision": safe_divide(tp_t, tp_t + fp_t),
            "Recall": safe_divide(tp_t, tp_t + fn_t),
            "Specificity": safe_divide(tn_t, tn_t + fp_t),
            "Exact_Class_Match": g["Class_Match"].mean(),
        })
    comparison_by_tag = pd.DataFrame(tag_rows)

    comparison_by_timestamp = comp.groupby("Timestamp").agg(
        Total_Tags=("Tag", "count"),
        Benchmark_Abnormal_Count=("Benchmark_Final_Status", lambda x: (x == "Abnormal").sum()),
        Without_Causal_Abnormal_Count=("Without_Causal_Final_Status", lambda x: (x == "Abnormal").sum()),
        Binary_Match_Count=("Binary_Match", "sum"),
        Class_Match_Count=("Class_Match", "sum"),
    ).reset_index()
    comparison_by_timestamp["Binary_Match_Rate"] = comparison_by_timestamp["Binary_Match_Count"] / comparison_by_timestamp["Total_Tags"]
    comparison_by_timestamp["Class_Match_Rate"] = comparison_by_timestamp["Class_Match_Count"] / comparison_by_timestamp["Total_Tags"]

    return {
        "Binary_Summary": binary_summary,
        "Class_Comparison": class_comparison,
        "Binary_Comparison": binary_comparison,
        "Comparison_By_Tag": comparison_by_tag,
        "Comparison_By_Timestamp": comparison_by_timestamp,
        "Comparison_Row_Tag": comp,
        "Disagreements": comp[~comp["Binary_Match"]].copy(),
    }


def create_summary(df, tag_cols, stable_rows, bands_df, all_results, cfg, comparison=None):
    total = len(all_results)
    abnormal = int((all_results["Final_Status"] == "Abnormal").sum())
    normal = int((all_results["Final_Status"] == "Normal").sum())

    rows = [
        {"Metric": "Method", "Value": "Without causal - regime-aware learned normal bands"},
        {"Metric": "Why improved", "Value": "Later clean-like periods are Normal if they fall inside learned normal value band."},
        {"Metric": "Total Raw Rows", "Value": len(df)},
        {"Metric": "Total Tags", "Value": len(tag_cols)},
        {"Metric": "Total Tag-Timestamp Points", "Value": total},
        {"Metric": "Normal Points", "Value": normal},
        {"Metric": "Abnormal Points", "Value": abnormal},
        {"Metric": "Abnormal Rate", "Value": safe_divide(abnormal, total)},
        {"Metric": "Stable Candidate Rows", "Value": int(stable_rows["Is_Global_Stable"].sum())},
        {"Metric": "Reference Bands Learned", "Value": len(bands_df)},
        {"Metric": "Max Reference Bands Per Tag", "Value": cfg["max_reference_bands"]},
        {"Metric": "Band K", "Value": cfg["band_k"]},
        {"Metric": "Soft Band Expand", "Value": cfg["soft_band_expand"]},
        {"Metric": "Persistence Window", "Value": cfg["persistence_window"]},
        {"Metric": "Persistence Min Points", "Value": cfg["persistence_min_points"]},
    ]

    if comparison is not None:
        for k, v in comparison["Binary_Summary"].iloc[0].to_dict().items():
            rows.append({"Metric": "Comparison - " + k, "Value": v})

    return pd.DataFrame(rows)


def write_excel(output_file, sheets):
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        for name, df in sheets.items():
            if df is not None:
                df.to_excel(writer, sheet_name=name[:31], index=False)
    print(f"Excel generated: {output_file}")


def main(cfg):
    print("Reading data...")
    df = read_excel_file(cfg["data_file"], cfg["data_sheet_name"])
    df = clean_column_names(df)

    timestamp_col = cfg["timestamp_col"]
    if timestamp_col not in df.columns:
        found = find_column(df, ["Timestamp", "Time", "DateTime", "Date"])
        if found is None:
            raise ValueError("Timestamp column not found.")
        timestamp_col = found

    df[timestamp_col] = pd.to_datetime(df[timestamp_col], errors="coerce")
    df = df.dropna(subset=[timestamp_col]).sort_values(timestamp_col).reset_index(drop=True)

    tag_cols = []
    for c in df.columns:
        if c == timestamp_col:
            continue
        numeric = pd.to_numeric(df[c], errors="coerce")
        if numeric.notna().sum() > 0:
            df[c] = numeric
            tag_cols.append(c)

    if not tag_cols:
        raise ValueError("No numeric tag columns found.")

    print(f"Rows: {len(df)} | Tags: {len(tag_cols)}")

    print("Detecting stable rows...")
    stable_rows = detect_global_stable_rows(df, tag_cols, timestamp_col, cfg)

    print("Learning normal value bands...")
    bands_df = learn_reference_bands(df, tag_cols, timestamp_col, stable_rows, cfg)

    print("Generating All_Results style output...")
    all_results = generate_all_results(df, tag_cols, timestamp_col, bands_df, cfg)
    row_status = create_row_status(all_results, timestamp_col)
    tag_summary = create_tag_summary(all_results)

    status_mapping = pd.DataFrame([
        {"Logic": "Inside learned normal band", "Final_Class": "Normal", "Final_Status": "Normal"},
        {"Logic": "Outside band but isolated and not strong", "Final_Class": "Normal", "Final_Status": "Normal"},
        {"Logic": "Outside band + persistent, distance_z 3.0 to 3.5", "Final_Class": "Drift", "Final_Status": "Abnormal"},
        {"Logic": "Outside band + persistent, distance_z 3.5 to 5.0", "Final_Class": "Drift + Anomaly", "Final_Status": "Abnormal"},
        {"Logic": "Very far outside band, distance_z >= 5.0", "Final_Class": "Strong Anomaly", "Final_Status": "Abnormal"},
    ])

    benchmark = load_benchmark_all_results(cfg["benchmark_file"], cfg["benchmark_sheet_name"])
    comparison = None
    if benchmark is not None:
        print("Comparing with benchmark...")
        comparison = compare_results(all_results, benchmark, timestamp_col)
    else:
        print("Benchmark comparison skipped.")

    summary = create_summary(df, tag_cols, stable_rows, bands_df, all_results, cfg, comparison)

    sheets = {
        "Summary": summary,
        "Status_Mapping": status_mapping,
        "Global_Stable_Rows": stable_rows,
        "Reference_Bands": bands_df,
        "Without_Causal_All_Results": all_results,
        "Row_Status": row_status,
        "Tag_Summary": tag_summary,
    }
    if comparison is not None:
        sheets.update(comparison)

    write_excel(cfg["output_file"], sheets)
    print("Completed.")


def parse_args():
    p = argparse.ArgumentParser(description="Regime-aware without-causal outlier detection")
    p.add_argument("--data_file", default=DEFAULTS["data_file"])
    p.add_argument("--benchmark_file", default=DEFAULTS["benchmark_file"])
    p.add_argument("--output_file", default=DEFAULTS["output_file"])
    p.add_argument("--data_sheet_name", default=DEFAULTS["data_sheet_name"])
    p.add_argument("--benchmark_sheet_name", default=DEFAULTS["benchmark_sheet_name"])
    p.add_argument("--timestamp_col", default=DEFAULTS["timestamp_col"])
    p.add_argument("--stable_quantile", type=float, default=DEFAULTS["stable_quantile"])
    p.add_argument("--max_reference_bands", type=int, default=DEFAULTS["max_reference_bands"])
    p.add_argument("--band_k", type=float, default=DEFAULTS["band_k"])
    p.add_argument("--soft_band_expand", type=float, default=DEFAULTS["soft_band_expand"])
    p.add_argument("--persistence_window", type=int, default=DEFAULTS["persistence_window"])
    p.add_argument("--persistence_min_points", type=int, default=DEFAULTS["persistence_min_points"])
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    cfg = DEFAULTS.copy()
    cfg.update(vars(args))
    main(cfg)
