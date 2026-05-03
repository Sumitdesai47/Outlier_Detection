"""
WITHOUT-CAUSAL OUTLIER DETECTION - CLEAN-ANCHORED IMPROVED V3

Why this version:
    The previous regime-aware version can overfit/learn wrong regimes as normal.
    This version improves the earlier clean-limit logic, but still stays anchored
    to the detected clean/reference behavior.

Main logic:
    1. Automatically detect one stable clean/reference window from all tags.
    2. For each tag, use that clean window as the anchor/reference.
    3. Search the full history for rows that are similar to the clean anchor for that tag.
       Example: if 2022 clean behavior and 2025 behavior are similar, 2025 points are added
       to that tag's normal reference population, so they will NOT be marked as drift.
    4. Calculate robust tag-wise limits from clean-like rows only.
    5. Use persistence logic so isolated small threshold crossings are not called drift.
    6. Generate Excel in All_Results-style format and optionally compare with benchmark.

Run:
    python without_causal_clean_anchored_improved_v3.py \
        --data_file "Multi_X_Multi_Y_Correct_Data.xlsx" \
        --benchmark_file "context_aware_outlier_results(1).xlsx" \
        --output_file "without_causal_clean_anchored_improved_v3_result.xlsx"

Recommended starting parameters:
    --candidate_z_limit 2.5 \
    --threshold_k 3.5 \
    --limit_margin 0.15 \
    --persistence_window 3 \
    --persistence_min_points 2

Required packages:
    pip install pandas numpy openpyxl
"""

import os
import argparse
import numpy as np
import pandas as pd


CONFIG = {
    "data_file": "Multi_X_Multi_Y_Correct_Data.xlsx",
    "data_sheet_name": None,
    "timestamp_col": "Timestamp",

    "benchmark_result_file": "",
    "benchmark_sheet_name": "All_Results",

    "output_file": "without_causal_clean_anchored_improved_v3_result.xlsx",

    # Auto clean window selection
    "clean_window_rows": None,          # Auto if None
    "preclean_level_z": 3.5,
    "preclean_jump_z": 4.0,
    "max_bad_tag_fraction": 0.10,

    # Clean-anchored candidate selection
    "candidate_z_limit": 2.5,           # Similar-to-clean value tolerance
    "candidate_quantile_buffer": 0.10,  # Expand clean quantile band by this fraction
    "min_candidate_rows": 30,
    "min_candidate_fraction": 0.05,

    # Final limit calculation
    "threshold_k": 3.5,                 # More stable than 3.0 for industrial data
    "limit_margin": 0.15,               # Add 15% width margin to reduce false drift
    "percentile_low": 0.005,            # 0.5 percentile of clean-like values
    "percentile_high": 0.995,           # 99.5 percentile of clean-like values

    # Final severity and persistence
    "drift_z": 2.0,                     # Distance from limit, not from mean
    "drift_anomaly_z": 3.5,
    "strong_anomaly_z": 5.0,
    "persistence_window": 3,
    "persistence_min_points": 2,
    "treat_isolated_soft_outside_as_normal": True,

    "std_epsilon": 1e-9,
}


# ============================================================
# Utility functions
# ============================================================

def read_excel_file(file_path, sheet_name=None):
    if sheet_name is None or str(sheet_name).strip() == "":
        return pd.read_excel(file_path)
    return pd.read_excel(file_path, sheet_name=sheet_name)


def clean_column_names(df):
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    return out


def find_column(df, possible_names):
    mapping = {str(c).strip().lower(): c for c in df.columns}
    for name in possible_names:
        if name.lower() in mapping:
            return mapping[name.lower()]
    for c in df.columns:
        cn = str(c).strip().lower().replace(" ", "_")
        for name in possible_names:
            nn = name.lower().replace(" ", "_")
            if cn == nn:
                return c
    return None


def safe_divide(a, b):
    if b == 0 or pd.isna(b):
        return np.nan
    return a / b


def robust_center_scale(s, std_epsilon=1e-9):
    s = pd.to_numeric(pd.Series(s), errors="coerce").dropna()
    if len(s) == 0:
        return np.nan, np.nan, np.nan
    median = s.median()
    mad = (s - median).abs().median()
    scale = 1.4826 * mad
    if pd.isna(scale) or scale < std_epsilon:
        scale = s.std()
    if pd.isna(scale) or scale < std_epsilon:
        scale = std_epsilon
    return median, scale, mad


def robust_zscore_frame(df_numeric, std_epsilon=1e-9):
    med = df_numeric.median(axis=0, skipna=True)
    mad = (df_numeric - med).abs().median(axis=0, skipna=True)
    scale = 1.4826 * mad
    std = df_numeric.std(axis=0, skipna=True)
    bad = (scale < std_epsilon) | (scale.isna())
    scale[bad] = std[bad]
    bad = (scale < std_epsilon) | (scale.isna())
    scale[bad] = 1.0
    return (df_numeric - med) / scale


def binary_status(final_class):
    if pd.isna(final_class):
        return "Unknown"
    x = str(final_class).strip().lower()
    if x in ["normal", "ok", "good"]:
        return "Normal"
    if x in ["unknown", "", "nan", "none"]:
        return "Unknown"
    return "Abnormal"


def classify_outside(distance_z, is_effective_outside, is_strong, config):
    if not is_effective_outside:
        return "Normal"
    if pd.isna(distance_z):
        return "Unknown"
    dz = abs(distance_z)
    if is_strong or dz >= config["strong_anomaly_z"]:
        return "Strong Anomaly"
    if dz < config["drift_z"]:
        return "Drift"
    if dz < config["drift_anomaly_z"]:
        return "Drift"
    if dz < config["strong_anomaly_z"]:
        return "Drift + Anomaly"
    return "Strong Anomaly"


# ============================================================
# Step 1: Auto detect global clean/reference window
# ============================================================

def detect_clean_window(df, tag_cols, timestamp_col, config):
    data = df[tag_cols].apply(pd.to_numeric, errors="coerce")
    n = len(data)

    if config["clean_window_rows"] is None:
        # Keep it long enough to be stable, but not so long that it covers multiple regimes
        win = max(30, min(180, int(n * 0.12)))
    else:
        win = int(config["clean_window_rows"])

    if win >= n:
        win = max(5, int(n * 0.25))

    level_z = robust_zscore_frame(data, config["std_epsilon"])
    level_bad_fraction = (level_z.abs() > config["preclean_level_z"]).mean(axis=1)

    jump = data.diff().abs().fillna(0)
    jump_z = robust_zscore_frame(jump, config["std_epsilon"])
    jump_bad_fraction = (jump_z.abs() > config["preclean_jump_z"]).mean(axis=1)

    clean_score = 0.75 * level_bad_fraction.fillna(1.0) + 0.25 * jump_bad_fraction.fillna(1.0)

    # Add a penalty if too many tags are abnormal in a row
    clean_score = clean_score + np.where(level_bad_fraction > config["max_bad_tag_fraction"], 0.25, 0.0)

    rolling_score = clean_score.rolling(win, min_periods=win).mean()
    best_end = rolling_score.idxmin()
    if pd.isna(best_end):
        raise ValueError("Could not detect clean window. Try smaller --clean_window_rows.")
    best_end = int(best_end)
    best_start = best_end - win + 1

    clean_df = df.iloc[best_start:best_end + 1].copy()

    score_df = pd.DataFrame({
        timestamp_col: df[timestamp_col],
        "Level_Bad_Fraction": level_bad_fraction,
        "Jump_Bad_Fraction": jump_bad_fraction,
        "Clean_Score": clean_score,
        "Rolling_Clean_Score": rolling_score,
        "Is_Selected_Clean_Window": False,
    })
    score_df.loc[best_start:best_end, "Is_Selected_Clean_Window"] = True

    clean_info = pd.DataFrame([{
        "Clean_Start_Index": best_start,
        "Clean_End_Index": best_end,
        "Clean_Start_Time": clean_df[timestamp_col].min(),
        "Clean_End_Time": clean_df[timestamp_col].max(),
        "Clean_Rows": len(clean_df),
        "Clean_Window_Rows_Used": win,
        "Method": "Auto-selected stable rolling window; used only as anchor, not final full reference",
    }])

    return clean_df, clean_info, score_df


# ============================================================
# Step 2: Clean-anchored tag-wise reference population
# ============================================================

def build_tag_reference_limits(df, clean_df, tag_cols, config):
    rows = []
    candidate_masks = {}

    for tag in tag_cols:
        full = pd.to_numeric(df[tag], errors="coerce")
        clean = pd.to_numeric(clean_df[tag], errors="coerce").dropna()

        if len(clean) == 0:
            continue

        clean_median, clean_scale, _ = robust_center_scale(clean, config["std_epsilon"])
        clean_mean = clean.mean()
        clean_std = clean.std()
        if pd.isna(clean_std) or clean_std < config["std_epsilon"]:
            clean_std = clean_scale

        clean_q05 = clean.quantile(0.05)
        clean_q95 = clean.quantile(0.95)
        clean_iqr = clean.quantile(0.75) - clean.quantile(0.25)
        if pd.isna(clean_iqr) or clean_iqr < config["std_epsilon"]:
            clean_iqr = clean_scale

        # Candidate rule A: similar robust distance to clean anchor
        anchor_z = (full - clean_median) / clean_scale
        mask_z = anchor_z.abs() <= config["candidate_z_limit"]

        # Candidate rule B: inside expanded clean percentile band
        buffer_width = config["candidate_quantile_buffer"] * max(clean_q95 - clean_q05, clean_iqr, clean_scale)
        expanded_low = clean_q05 - buffer_width
        expanded_high = clean_q95 + buffer_width
        mask_band = (full >= expanded_low) & (full <= expanded_high)

        # Candidate = rows that are close to clean behavior by either rule
        candidate_mask = (mask_z | mask_band) & full.notna()

        # If too few candidates, relax slightly but keep clean-anchor direction
        min_rows = max(config["min_candidate_rows"], int(len(full.dropna()) * config["min_candidate_fraction"]))
        if candidate_mask.sum() < min_rows:
            relaxed_z = max(config["candidate_z_limit"], 3.5)
            candidate_mask = ((anchor_z.abs() <= relaxed_z) | mask_band) & full.notna()

        # Still too few: use clean only as fallback
        if candidate_mask.sum() < max(5, config["min_candidate_rows"] // 2):
            ref_values = clean.copy()
            source = "Clean_Window_Only_Fallback"
            candidate_count = len(ref_values)
        else:
            ref_values = full[candidate_mask].dropna()
            source = "Clean_Anchored_Similar_Historical_Rows"
            candidate_count = int(candidate_mask.sum())

        # Robustly trim candidate population one more time to avoid abnormal points inside relaxed pool
        ref_median_1, ref_scale_1, _ = robust_center_scale(ref_values, config["std_epsilon"])
        ref_z_1 = (ref_values - ref_median_1) / ref_scale_1
        core_ref = ref_values[ref_z_1.abs() <= 4.0]
        if len(core_ref) >= max(10, int(0.60 * len(ref_values))):
            ref_values = core_ref

        ref_median, ref_scale, _ = robust_center_scale(ref_values, config["std_epsilon"])
        ref_mean = ref_values.mean()
        ref_std = ref_values.std()
        if pd.isna(ref_std) or ref_std < config["std_epsilon"]:
            ref_std = ref_scale

        robust_lower = ref_median - config["threshold_k"] * ref_scale
        robust_upper = ref_median + config["threshold_k"] * ref_scale

        p_low = ref_values.quantile(config["percentile_low"])
        p_high = ref_values.quantile(config["percentile_high"])

        # Combine robust limit and percentile coverage.
        # Use the wider of both so clean-like later periods do not become false drift.
        lower = min(robust_lower, p_low)
        upper = max(robust_upper, p_high)

        width = upper - lower
        if pd.isna(width) or width <= config["std_epsilon"]:
            width = max(ref_scale, config["std_epsilon"])

        lower = lower - config["limit_margin"] * width
        upper = upper + config["limit_margin"] * width

        # Scale used for severity outside the final limit
        severity_scale = max(ref_scale, config["std_epsilon"])

        rows.append({
            "Tag": tag,
            "Reference_Source": source,
            "Clean_Count": len(clean),
            "Candidate_Count_Before_Trim": candidate_count,
            "Reference_Count_Final": len(ref_values),
            "Candidate_Fraction_Of_All_Rows": safe_divide(candidate_count, full.notna().sum()),
            "Clean_Mean": clean_mean,
            "Clean_Median": clean_median,
            "Clean_Std": clean_std,
            "Clean_Robust_Scale": clean_scale,
            "Reference_Mean": ref_mean,
            "Reference_Median": ref_median,
            "Reference_Std": ref_std,
            "Reference_Robust_Scale": ref_scale,
            "Severity_Scale": severity_scale,
            "Candidate_Z_Limit": config["candidate_z_limit"],
            "Threshold_K": config["threshold_k"],
            "Limit_Margin": config["limit_margin"],
            "Lower_Limit": lower,
            "Upper_Limit": upper,
            "Initial_Clean_Lower": clean_median - config["threshold_k"] * clean_scale,
            "Initial_Clean_Upper": clean_median + config["threshold_k"] * clean_scale,
        })

        candidate_masks[tag] = candidate_mask

    limits = pd.DataFrame(rows)
    return limits, candidate_masks


# ============================================================
# Step 3: Generate All_Results style output
# ============================================================

def generate_without_causal_results(df, tag_cols, timestamp_col, limits_df, config):
    limits_map = limits_df.set_index("Tag").to_dict(orient="index")
    outputs = []

    for tag in tag_cols:
        if tag not in limits_map:
            continue

        lim = limits_map[tag]
        lower = lim["Lower_Limit"]
        upper = lim["Upper_Limit"]
        center = lim["Reference_Median"]
        scale = lim["Severity_Scale"]

        temp = df[[timestamp_col, tag]].copy()
        temp.rename(columns={tag: "Actual_Value"}, inplace=True)
        temp["Tag"] = tag
        temp["Actual_Value"] = pd.to_numeric(temp["Actual_Value"], errors="coerce")

        temp["Reference_Mean"] = lim["Reference_Mean"]
        temp["Reference_Median"] = lim["Reference_Median"]
        temp["Reference_Std"] = lim["Reference_Std"]
        temp["Reference_Robust_Scale"] = lim["Reference_Robust_Scale"]
        temp["Lower_Limit"] = lower
        temp["Upper_Limit"] = upper

        temp["Value_Z_To_Reference"] = (temp["Actual_Value"] - center) / scale

        temp["Raw_Outside_Limit"] = (temp["Actual_Value"] < lower) | (temp["Actual_Value"] > upper)

        # Distance from final limit. Inside band = 0.
        temp["Distance_From_Limit"] = 0.0
        below = temp["Actual_Value"] < lower
        above = temp["Actual_Value"] > upper
        temp.loc[below, "Distance_From_Limit"] = temp.loc[below, "Actual_Value"] - lower
        temp.loc[above, "Distance_From_Limit"] = temp.loc[above, "Actual_Value"] - upper
        temp["Distance_Z_From_Limit"] = temp["Distance_From_Limit"] / scale
        temp["Abs_Distance_Z_From_Limit"] = temp["Distance_Z_From_Limit"].abs()

        outside_int = temp["Raw_Outside_Limit"].astype(int)
        temp["Outside_Count_In_Window"] = outside_int.rolling(
            window=config["persistence_window"],
            min_periods=1,
            center=True
        ).sum()

        temp["Persistent_Outside"] = temp["Outside_Count_In_Window"] >= config["persistence_min_points"]
        temp["Strong_Outside"] = temp["Abs_Distance_Z_From_Limit"] >= config["strong_anomaly_z"]

        if config["treat_isolated_soft_outside_as_normal"]:
            temp["Effective_Outside"] = temp["Strong_Outside"] | (temp["Raw_Outside_Limit"] & temp["Persistent_Outside"])
        else:
            temp["Effective_Outside"] = temp["Raw_Outside_Limit"]

        temp["Limit_Status"] = np.select(
            [
                temp["Actual_Value"] < lower,
                temp["Actual_Value"] > upper,
                temp["Actual_Value"].between(lower, upper, inclusive="both")
            ],
            [
                "Below Lower Limit",
                "Above Upper Limit",
                "Within Limit"
            ],
            default="Unknown"
        )

        temp["Direction"] = np.select(
            [temp["Actual_Value"] < lower, temp["Actual_Value"] > upper],
            ["Down", "Up"],
            default="Normal"
        )

        temp["Final_Class"] = temp.apply(
            lambda r: classify_outside(
                r["Distance_Z_From_Limit"],
                r["Effective_Outside"],
                r["Strong_Outside"],
                config
            ),
            axis=1
        )
        temp["Final_Status"] = temp["Final_Class"].apply(binary_status)

        # Causal-style blank fields
        temp["Predicted_Value"] = np.nan
        temp["Residual"] = np.nan
        temp["Residual_Z"] = np.nan
        temp["Method"] = "Without Causal - Clean Anchored Similar-History Limits V3"

        temp = temp[[
            timestamp_col,
            "Tag",
            "Actual_Value",
            "Predicted_Value",
            "Residual",
            "Residual_Z",
            "Reference_Mean",
            "Reference_Median",
            "Reference_Std",
            "Reference_Robust_Scale",
            "Lower_Limit",
            "Upper_Limit",
            "Value_Z_To_Reference",
            "Distance_Z_From_Limit",
            "Abs_Distance_Z_From_Limit",
            "Limit_Status",
            "Direction",
            "Raw_Outside_Limit",
            "Outside_Count_In_Window",
            "Persistent_Outside",
            "Strong_Outside",
            "Effective_Outside",
            "Final_Class",
            "Final_Status",
            "Method",
        ]]

        outputs.append(temp)

    return pd.concat(outputs, ignore_index=True)


# ============================================================
# Summary outputs
# ============================================================

def create_row_status(all_results, timestamp_col):
    out = all_results.groupby(timestamp_col).agg(
        Total_Tags=("Tag", "count"),
        Abnormal_Tag_Count=("Final_Status", lambda x: (x == "Abnormal").sum()),
        Normal_Tag_Count=("Final_Status", lambda x: (x == "Normal").sum()),
        Drift_Count=("Final_Class", lambda x: (x == "Drift").sum()),
        Drift_Anomaly_Count=("Final_Class", lambda x: (x == "Drift + Anomaly").sum()),
        Strong_Anomaly_Count=("Final_Class", lambda x: (x == "Strong Anomaly").sum()),
    ).reset_index()
    out["Abnormal_Tag_Rate"] = out["Abnormal_Tag_Count"] / out["Total_Tags"]
    out["Row_Final_Status"] = np.where(out["Abnormal_Tag_Count"] > 0, "Abnormal", "Normal")
    return out


def create_tag_summary(all_results):
    out = all_results.groupby("Tag").agg(
        Total_Rows=("Final_Status", "count"),
        Normal_Count=("Final_Status", lambda x: (x == "Normal").sum()),
        Abnormal_Count=("Final_Status", lambda x: (x == "Abnormal").sum()),
        Drift_Count=("Final_Class", lambda x: (x == "Drift").sum()),
        Drift_Anomaly_Count=("Final_Class", lambda x: (x == "Drift + Anomaly").sum()),
        Strong_Anomaly_Count=("Final_Class", lambda x: (x == "Strong Anomaly").sum()),
    ).reset_index()
    out["Abnormal_Rate"] = out["Abnormal_Count"] / out["Total_Rows"]
    return out.sort_values("Abnormal_Rate", ascending=False)


# ============================================================
# Benchmark comparison
# ============================================================

def load_benchmark_all_results(file_path, sheet_name="All_Results"):
    if file_path is None or str(file_path).strip() == "":
        return None
    if not os.path.exists(file_path):
        print(f"Benchmark file not found: {file_path}")
        return None

    xl = pd.ExcelFile(file_path)
    if sheet_name in xl.sheet_names:
        return clean_column_names(pd.read_excel(file_path, sheet_name=sheet_name))

    for s in xl.sheet_names:
        if "all" in s.lower() and "result" in s.lower():
            return clean_column_names(pd.read_excel(file_path, sheet_name=s))

    print("Benchmark All_Results sheet not found. Skipping comparison.")
    return None


def standardize_benchmark_columns(bench_df):
    ts = find_column(bench_df, ["Timestamp", "Time", "DateTime", "Date"])
    tag = find_column(bench_df, ["Tag", "Target_Tag", "Variable", "Column", "Parameter"])
    cls = find_column(bench_df, ["Final_Class", "Final Class", "Class", "Status"])
    status = find_column(bench_df, ["Final_Status", "Final Status", "Binary_Status"])

    if ts is None:
        raise ValueError("Benchmark timestamp column not found.")
    if tag is None:
        raise ValueError("Benchmark tag column not found.")
    if cls is None:
        raise ValueError("Benchmark Final_Class/Class/Status column not found.")

    out = bench_df[[ts, tag, cls]].copy()
    out.columns = ["Timestamp", "Tag", "Benchmark_Final_Class"]

    if status is not None:
        out["Benchmark_Final_Status"] = bench_df[status].apply(binary_status)
    else:
        out["Benchmark_Final_Status"] = out["Benchmark_Final_Class"].apply(binary_status)

    out["Timestamp"] = pd.to_datetime(out["Timestamp"], errors="coerce")
    out["Tag"] = out["Tag"].astype(str).str.strip()
    out = out.dropna(subset=["Timestamp", "Tag"])
    return out


def compare_results(all_results, benchmark_df, timestamp_col):
    wc = all_results.rename(columns={timestamp_col: "Timestamp"}).copy()
    wc["Timestamp"] = pd.to_datetime(wc["Timestamp"], errors="coerce")
    wc["Tag"] = wc["Tag"].astype(str).str.strip()

    wc_small = wc[[
        "Timestamp", "Tag", "Actual_Value", "Lower_Limit", "Upper_Limit",
        "Distance_Z_From_Limit", "Limit_Status", "Direction", "Final_Class", "Final_Status"
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
        default="Other"
    )

    tp = ((comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Without_Causal_Final_Status"] == "Abnormal")).sum()
    tn = ((comp["Benchmark_Final_Status"] == "Normal") & (comp["Without_Causal_Final_Status"] == "Normal")).sum()
    fp = ((comp["Benchmark_Final_Status"] == "Normal") & (comp["Without_Causal_Final_Status"] == "Abnormal")).sum()
    fn = ((comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Without_Causal_Final_Status"] == "Normal")).sum()
    total = len(comp)

    binary_summary = pd.DataFrame([{
        "Total_Matched_Rows": total,
        "TP_Both_Abnormal": int(tp),
        "TN_Both_Normal": int(tn),
        "FP_Without_Causal_Only": int(fp),
        "FN_Benchmark_Only": int(fn),
        "Benchmark_Abnormal_Rows": int((comp["Benchmark_Final_Status"] == "Abnormal").sum()),
        "Without_Causal_Abnormal_Rows": int((comp["Without_Causal_Final_Status"] == "Abnormal").sum()),
        "Binary_Agreement_Accuracy": safe_divide(tp + tn, total),
        "Precision_vs_Benchmark": safe_divide(tp, tp + fp),
        "Recall_vs_Benchmark": safe_divide(tp, tp + fn),
        "Specificity_vs_Benchmark": safe_divide(tn, tn + fp),
        "Exact_Final_Class_Match": comp["Class_Match"].mean() if total > 0 else np.nan,
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
            "TP_Both_Abnormal": int(tp_t),
            "TN_Both_Normal": int(tn_t),
            "FP_Without_Causal_Only": int(fp_t),
            "FN_Benchmark_Only": int(fn_t),
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

    disagreements = comp[~comp["Binary_Match"]].copy()

    return {
        "Binary_Summary": binary_summary,
        "Class_Comparison": class_comparison,
        "Binary_Comparison": binary_comparison,
        "Comparison_By_Tag": comparison_by_tag,
        "Comparison_By_Timestamp": comparison_by_timestamp,
        "Comparison_Row_Tag": comp,
        "Disagreements": disagreements,
    }


def create_summary(df, tag_cols, clean_info, limits_df, all_results, comparison_outputs, config):
    total = len(all_results)
    abnormal = int((all_results["Final_Status"] == "Abnormal").sum())
    normal = int((all_results["Final_Status"] == "Normal").sum())

    rows = [
        {"Metric": "Method", "Value": "Without causal - clean anchored similar-history limits V3"},
        {"Metric": "Improvement", "Value": "Uses detected clean window as anchor, then adds historical rows similar to clean behavior for each tag."},
        {"Metric": "Why this helps", "Value": "Later periods similar to clean/reference behavior are included in normal reference and not marked as drift."},
        {"Metric": "Total Raw Rows", "Value": len(df)},
        {"Metric": "Total Tags", "Value": len(tag_cols)},
        {"Metric": "Total Tag-Timestamp Points", "Value": total},
        {"Metric": "Normal Points", "Value": normal},
        {"Metric": "Abnormal Points", "Value": abnormal},
        {"Metric": "Abnormal Rate", "Value": safe_divide(abnormal, total)},
        {"Metric": "Clean Start", "Value": clean_info.loc[0, "Clean_Start_Time"]},
        {"Metric": "Clean End", "Value": clean_info.loc[0, "Clean_End_Time"]},
        {"Metric": "Clean Rows", "Value": clean_info.loc[0, "Clean_Rows"]},
        {"Metric": "Candidate Z Limit", "Value": config["candidate_z_limit"]},
        {"Metric": "Threshold K", "Value": config["threshold_k"]},
        {"Metric": "Limit Margin", "Value": config["limit_margin"]},
        {"Metric": "Persistence Window", "Value": config["persistence_window"]},
        {"Metric": "Persistence Min Points", "Value": config["persistence_min_points"]},
    ]
    if comparison_outputs is not None:
        for k, v in comparison_outputs["Binary_Summary"].iloc[0].to_dict().items():
            rows.append({"Metric": f"Comparison - {k}", "Value": v})
    return pd.DataFrame(rows)


def write_excel(output_file, sheets):
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        for name, df in sheets.items():
            if df is None:
                continue
            df.to_excel(writer, sheet_name=name[:31], index=False)
    print(f"Excel generated: {output_file}")


# ============================================================
# Main
# ============================================================

def main(config):
    print("Reading data...")
    df = read_excel_file(config["data_file"], config["data_sheet_name"])
    df = clean_column_names(df)

    timestamp_col = config["timestamp_col"]
    if timestamp_col not in df.columns:
        detected = find_column(df, ["Timestamp", "Time", "DateTime", "Date"])
        if detected is None:
            raise ValueError("Timestamp column not found in raw data.")
        timestamp_col = detected

    df[timestamp_col] = pd.to_datetime(df[timestamp_col], errors="coerce")
    df = df.dropna(subset=[timestamp_col]).sort_values(timestamp_col).reset_index(drop=True)

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

    print(f"Rows: {len(df)}")
    print(f"Numeric tags: {len(tag_cols)}")

    print("Detecting clean anchor window...")
    clean_df, clean_info, clean_score = detect_clean_window(df, tag_cols, timestamp_col, config)

    print("Building clean-anchored tag-wise limits...")
    limits_df, _ = build_tag_reference_limits(df, clean_df, tag_cols, config)

    print("Generating All_Results format...")
    all_results = generate_without_causal_results(df, tag_cols, timestamp_col, limits_df, config)

    print("Creating status summaries...")
    row_status = create_row_status(all_results, timestamp_col)
    tag_summary = create_tag_summary(all_results)

    benchmark_df = load_benchmark_all_results(config["benchmark_result_file"], config["benchmark_sheet_name"])
    comparison_outputs = None
    if benchmark_df is not None:
        print("Comparing with benchmark...")
        comparison_outputs = compare_results(all_results, benchmark_df, timestamp_col)
    else:
        print("Benchmark comparison skipped.")

    summary = create_summary(df, tag_cols, clean_info, limits_df, all_results, comparison_outputs, config)

    status_mapping = pd.DataFrame([
        {"Condition": "Inside final clean-anchored limits", "Final_Class": "Normal", "Final_Status": "Normal"},
        {"Condition": "Outside limits but isolated and not strong", "Final_Class": "Normal", "Final_Status": "Normal"},
        {"Condition": "Outside limits and persistent", "Final_Class": "Drift / Drift + Anomaly based on distance", "Final_Status": "Abnormal"},
        {"Condition": "Very far outside limit", "Final_Class": "Strong Anomaly", "Final_Status": "Abnormal"},
    ])

    sheets = {
        "Summary": summary,
        "Status_Mapping": status_mapping,
        "Auto_Clean_Anchor": clean_info,
        "Clean_Score_By_Row": clean_score,
        "Reference_Limits": limits_df,
        "Without_Causal_All_Results": all_results,
        "Row_Status": row_status,
        "Tag_Summary": tag_summary,
    }

    if comparison_outputs is not None:
        sheets.update({
            "Binary_Summary": comparison_outputs["Binary_Summary"],
            "Class_Comparison": comparison_outputs["Class_Comparison"],
            "Binary_Comparison": comparison_outputs["Binary_Comparison"],
            "Comparison_By_Tag": comparison_outputs["Comparison_By_Tag"],
            "Comparison_By_Timestamp": comparison_outputs["Comparison_By_Timestamp"],
            "Comparison_Row_Tag": comparison_outputs["Comparison_Row_Tag"],
            "Disagreements": comparison_outputs["Disagreements"],
        })

    print("Writing Excel...")
    write_excel(config["output_file"], sheets)
    print("Completed.")

    return {
        "summary": summary,
        "clean_info": clean_info,
        "limits": limits_df,
        "all_results": all_results,
        "comparison_outputs": comparison_outputs,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Without causal clean-anchored outlier detection V3")
    parser.add_argument("--data_file", type=str, default=CONFIG["data_file"])
    parser.add_argument("--data_sheet_name", type=str, default=None)
    parser.add_argument("--timestamp_col", type=str, default=CONFIG["timestamp_col"])
    parser.add_argument("--benchmark_file", type=str, default=CONFIG["benchmark_result_file"])
    parser.add_argument("--benchmark_sheet_name", type=str, default=CONFIG["benchmark_sheet_name"])
    parser.add_argument("--output_file", type=str, default=CONFIG["output_file"])

    parser.add_argument("--clean_window_rows", type=int, default=None)
    parser.add_argument("--candidate_z_limit", type=float, default=CONFIG["candidate_z_limit"])
    parser.add_argument("--threshold_k", type=float, default=CONFIG["threshold_k"])
    parser.add_argument("--limit_margin", type=float, default=CONFIG["limit_margin"])
    parser.add_argument("--persistence_window", type=int, default=CONFIG["persistence_window"])
    parser.add_argument("--persistence_min_points", type=int, default=CONFIG["persistence_min_points"])
    parser.add_argument("--min_candidate_rows", type=int, default=CONFIG["min_candidate_rows"])

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    CONFIG["data_file"] = args.data_file
    CONFIG["data_sheet_name"] = args.data_sheet_name
    CONFIG["timestamp_col"] = args.timestamp_col
    CONFIG["benchmark_result_file"] = args.benchmark_file
    CONFIG["benchmark_sheet_name"] = args.benchmark_sheet_name
    CONFIG["output_file"] = args.output_file
    CONFIG["clean_window_rows"] = args.clean_window_rows
    CONFIG["candidate_z_limit"] = args.candidate_z_limit
    CONFIG["threshold_k"] = args.threshold_k
    CONFIG["limit_margin"] = args.limit_margin
    CONFIG["persistence_window"] = args.persistence_window
    CONFIG["persistence_min_points"] = args.persistence_min_points
    CONFIG["min_candidate_rows"] = args.min_candidate_rows

    main(CONFIG)
