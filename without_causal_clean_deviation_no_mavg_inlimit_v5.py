"""
WITHOUT-CAUSAL OUTLIER DETECTION - V5
Clean-anchored limits + deviation spike/change + within-limit outlier detection.

Main points:
    1. Does NOT use moving average / rolling average.
    2. Detects outside-limit outliers using clean-like reference limits.
    3. Detects within-limit outliers using:
        - sudden value/deviation spike
        - sudden error/deviation change
        - high deviation inside broad limits but outside central clean-like band
    4. Uses run-length persistence, not moving average.
    5. Exports All_Results-style Excel output and optional benchmark comparison.

Run:
    python without_causal_clean_deviation_no_mavg_inlimit_v5.py \
        --data_file "Multi_X_Multi_Y_Correct_Data.xlsx" \
        --benchmark_file "context_aware_outlier_results(1).xlsx" \
        --output_file "without_causal_v5_no_mavg_inlimit_result.xlsx"

Install requirements:
    pip install pandas numpy openpyxl
"""

import os
import argparse
import numpy as np
import pandas as pd


# ============================================================
# DEFAULT CONFIG
# ============================================================

CONFIG = {
    "data_file": "Multi_X_Multi_Y_Correct_Data.xlsx",
    "benchmark_result_file": "",
    "data_sheet_name": None,
    "benchmark_sheet_name": "All_Results",
    "timestamp_col": "Timestamp",
    "output_file": "without_causal_v5_no_mavg_inlimit_result.xlsx",

    # Clean period detection - no moving average
    "global_level_z_limit": 3.5,
    "global_delta_z_limit": 4.0,
    "global_error_change_z_limit": 4.0,
    "stable_score_quantile": 0.35,
    "min_clean_rows": 30,

    # Clean-like reference expansion
    "candidate_z_limit": 2.75,
    "candidate_delta_z_limit": 4.0,
    "candidate_error_change_z_limit": 4.0,
    "min_reference_rows": 30,

    # Limits
    "threshold_k": 3.5,
    "limit_margin": 0.12,
    "central_quantile_low": 0.05,
    "central_quantile_high": 0.95,

    # Within-limit outlier detection
    "delta_spike_z": 4.0,
    "error_change_z": 4.0,
    "inlimit_deviation_z": 3.0,

    # Persistence using consecutive run length, not rolling/moving average
    "outside_persistence_points": 2,
    "inlimit_deviation_persistence_points": 2,

    # Severity thresholds (|z| vs clean baseline for drift / drift+anomaly / strong)
    "drift_z": 4.0,
    "drift_anomaly_z": 4.5,
    "strong_anomaly_z": 7.0,

    "std_epsilon": 1e-9,
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_column_names(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def read_excel_file(path, sheet_name=None):
    if sheet_name is None or str(sheet_name).strip() == "":
        return pd.read_excel(path)
    return pd.read_excel(path, sheet_name=sheet_name)


def find_column(df, possible_names):
    col_map = {str(c).strip().lower(): c for c in df.columns}
    for name in possible_names:
        if name.lower() in col_map:
            return col_map[name.lower()]

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


def robust_center_scale(values, eps=1e-9):
    """Median + MAD scale with std fallback."""
    s = pd.Series(values).dropna().astype(float)
    if len(s) == 0:
        return np.nan, np.nan

    med = s.median()
    mad = (s - med).abs().median()
    scale = 1.4826 * mad

    if pd.isna(scale) or scale < eps:
        scale = s.std()

    if pd.isna(scale) or scale < eps:
        scale = eps

    return med, scale


def robust_z_series(series, ref_values=None, eps=1e-9):
    """
    Robust z-score for a series.
    If ref_values is provided, center/scale are learned from ref_values.
    """
    s = pd.to_numeric(series, errors="coerce")
    ref = s if ref_values is None else pd.to_numeric(pd.Series(ref_values), errors="coerce")
    center, scale = robust_center_scale(ref, eps)
    return (s - center) / scale, center, scale


def robust_z_dataframe(df_num, eps=1e-9):
    out = pd.DataFrame(index=df_num.index)
    for c in df_num.columns:
        out[c], _, _ = robust_z_series(df_num[c], eps=eps)
    return out


def consecutive_run_lengths(flag_series):
    """
    Consecutive run length for True values.
    No moving average / no rolling logic.
    Each True inside the same consecutive segment receives that segment length.
    False values receive 0.
    """
    flag = pd.Series(flag_series).fillna(False).astype(bool)
    group_id = (flag != flag.shift()).cumsum()
    run_len = flag.groupby(group_id).transform("size")
    return run_len.where(flag, 0).astype(int)


def binary_status(final_class):
    if pd.isna(final_class):
        return "Unknown"
    fc = str(final_class).strip().lower()
    if fc in ["normal", "ok", "good"]:
        return "Normal"
    if fc in ["unknown", "", "nan", "none"]:
        return "Unknown"
    return "Abnormal"


# ============================================================
# CLEAN PERIOD DETECTION WITHOUT MOVING AVG
# ============================================================

def detect_clean_period_no_mavg(df, tag_cols, timestamp_col, config):
    """
    Detects clean reference rows without using moving average.

    Logic:
        1. Calculate row-wise fraction of bad tags using:
            - value level z
            - first-difference/delta z
            - error-change z
        2. Mark stable rows using quantile threshold.
        3. Pick the longest consecutive stable segment as clean period.
        4. If no long segment exists, use best stable rows as fallback.

    No rolling mean / moving average is used.
    """
    eps = config["std_epsilon"]
    data = df[tag_cols].apply(pd.to_numeric, errors="coerce")

    level_z = robust_z_dataframe(data, eps=eps)

    delta = data.diff()
    delta_z = robust_z_dataframe(delta.fillna(0), eps=eps)

    # Error change is same as change in deviation from robust global center.
    # Since center is fixed, this is equivalent to signed delta, but kept separately
    # for clear output and logic.
    global_medians = data.median(axis=0, skipna=True)
    error = data - global_medians
    error_change = error.diff()
    error_change_z = robust_z_dataframe(error_change.fillna(0), eps=eps)

    level_bad_fraction = (level_z.abs() > config["global_level_z_limit"]).mean(axis=1)
    delta_bad_fraction = (delta_z.abs() > config["global_delta_z_limit"]).mean(axis=1)
    error_change_bad_fraction = (error_change_z.abs() > config["global_error_change_z_limit"]).mean(axis=1)

    clean_score = (
        0.50 * level_bad_fraction.fillna(1.0)
        + 0.25 * delta_bad_fraction.fillna(1.0)
        + 0.25 * error_change_bad_fraction.fillna(1.0)
    )

    cutoff = clean_score.quantile(config["stable_score_quantile"])
    stable_flag = clean_score <= cutoff

    # Prefer rows with no sudden change/spike.
    stable_flag = stable_flag & (delta_bad_fraction <= 0.05) & (error_change_bad_fraction <= 0.05)

    run_len = consecutive_run_lengths(stable_flag)
    max_run = int(run_len.max()) if len(run_len) else 0

    clean_mode = "Longest consecutive stable segment"

    if max_run >= config["min_clean_rows"]:
        best_group = ((stable_flag != stable_flag.shift()).cumsum())[run_len.idxmax()]
        selected = ((stable_flag != stable_flag.shift()).cumsum() == best_group) & stable_flag
    else:
        # Fallback: still no moving average. Select lowest-score stable rows.
        clean_mode = "Lowest clean-score rows fallback because no long stable segment was found"
        n_select = min(max(config["min_clean_rows"], int(len(df) * 0.10)), len(df))
        selected_idx = clean_score.sort_values().head(n_select).index
        selected = pd.Series(False, index=df.index)
        selected.loc[selected_idx] = True

    clean_df = df.loc[selected].copy()

    clean_score_df = pd.DataFrame({
        timestamp_col: df[timestamp_col],
        "Level_Bad_Fraction": level_bad_fraction,
        "Delta_Spike_Bad_Fraction": delta_bad_fraction,
        "Error_Change_Bad_Fraction": error_change_bad_fraction,
        "Clean_Score": clean_score,
        "Clean_Score_Cutoff": cutoff,
        "Is_Stable_Candidate": stable_flag,
        "Stable_Run_Length": run_len,
        "Is_Selected_Clean_Period": selected,
    })

    clean_info = pd.DataFrame([{
        "Clean_Method": clean_mode,
        "Clean_Start_Time": clean_df[timestamp_col].min(),
        "Clean_End_Time": clean_df[timestamp_col].max(),
        "Clean_Rows": len(clean_df),
        "Stable_Score_Quantile": config["stable_score_quantile"],
        "Clean_Score_Cutoff": cutoff,
        "Global_Level_Z_Limit": config["global_level_z_limit"],
        "Global_Delta_Z_Limit": config["global_delta_z_limit"],
        "Global_Error_Change_Z_Limit": config["global_error_change_z_limit"],
        "Moving_Average_Used": "No",
    }])

    return clean_df, clean_info, clean_score_df


# ============================================================
# TAG-WISE CLEAN-LIKE REFERENCE AND LIMITS
# ============================================================

def build_clean_like_limits(df, clean_df, clean_score_df, tag_cols, timestamp_col, config):
    """
    For each tag:
        1. Use selected clean period as anchor.
        2. Add historical points similar to clean anchor.
        3. Exclude value-spike and error-change spike rows.
        4. Build robust broad limits and central band.
    """
    eps = config["std_epsilon"]
    rows = []

    for tag in tag_cols:
        full = pd.to_numeric(df[tag], errors="coerce")
        clean_values = pd.to_numeric(clean_df[tag], errors="coerce").dropna()

        if len(clean_values) < 5:
            clean_values = full.dropna()

        clean_median, clean_scale = robust_center_scale(clean_values, eps)
        clean_mean = clean_values.mean()
        clean_std = clean_values.std()
        if pd.isna(clean_std) or clean_std < eps:
            clean_std = clean_scale

        # Candidate values similar to clean anchor
        value_z_to_clean = (full - clean_median) / clean_scale

        # Spike/change based on clean-period delta/error-change baseline
        full_delta = full.diff()
        clean_delta = pd.to_numeric(clean_df[tag], errors="coerce").diff().dropna()
        delta_z_to_clean, delta_center, delta_scale = robust_z_series(full_delta.fillna(0), clean_delta, eps=eps)

        full_error = full - clean_median
        full_error_change = full_error.diff()
        clean_error = pd.to_numeric(clean_df[tag], errors="coerce") - clean_median
        clean_error_change = clean_error.diff().dropna()
        error_change_z_to_clean, errchg_center, errchg_scale = robust_z_series(
            full_error_change.fillna(0), clean_error_change, eps=eps
        )

        clean_like_flag = (
            (value_z_to_clean.abs() <= config["candidate_z_limit"])
            & (delta_z_to_clean.abs() <= config["candidate_delta_z_limit"])
            & (error_change_z_to_clean.abs() <= config["candidate_error_change_z_limit"])
        )

        ref_values = full[clean_like_flag].dropna()

        # Fallback if too few clean-like points
        if len(ref_values) < config["min_reference_rows"]:
            ref_values = clean_values.copy()

        ref_median, ref_scale = robust_center_scale(ref_values, eps)
        ref_mean = ref_values.mean()
        ref_std = ref_values.std()
        if pd.isna(ref_std) or ref_std < eps:
            ref_std = ref_scale

        q01 = ref_values.quantile(0.01)
        q05 = ref_values.quantile(config["central_quantile_low"])
        q25 = ref_values.quantile(0.25)
        q75 = ref_values.quantile(0.75)
        q95 = ref_values.quantile(config["central_quantile_high"])
        q99 = ref_values.quantile(0.99)

        # Broad clean-like limits.
        robust_lower = ref_median - config["threshold_k"] * ref_scale
        robust_upper = ref_median + config["threshold_k"] * ref_scale
        lower = min(robust_lower, q01)
        upper = max(robust_upper, q99)

        # Margin prevents false drift around clean-like boundary.
        width = upper - lower
        if pd.isna(width) or width <= eps:
            width = max(ref_scale, eps)
        lower = lower - config["limit_margin"] * width
        upper = upper + config["limit_margin"] * width

        # Central band is used for within-limit deviation detection.
        central_lower = q05
        central_upper = q95

        clean_abs_error = (clean_values - clean_median).abs()
        abs_error_center, abs_error_scale = robust_center_scale(clean_abs_error, eps)

        rows.append({
            "Tag": tag,
            "Clean_Count": len(clean_values),
            "Clean_Mean": clean_mean,
            "Clean_Median": clean_median,
            "Clean_Std": clean_std,
            "Clean_Scale_MAD": clean_scale,
            "Clean_Like_Count": len(ref_values),
            "Reference_Mean": ref_mean,
            "Reference_Median": ref_median,
            "Reference_Std": ref_std,
            "Reference_Scale_MAD": ref_scale,
            "Reference_Q01": q01,
            "Reference_Q05": q05,
            "Reference_Q25": q25,
            "Reference_Q75": q75,
            "Reference_Q95": q95,
            "Reference_Q99": q99,
            "Lower_Limit": lower,
            "Upper_Limit": upper,
            "Central_Lower": central_lower,
            "Central_Upper": central_upper,
            "Delta_Center": delta_center,
            "Delta_Scale": delta_scale,
            "Error_Change_Center": errchg_center,
            "Error_Change_Scale": errchg_scale,
            "Abs_Error_Center": abs_error_center,
            "Abs_Error_Scale": abs_error_scale,
            "Threshold_K": config["threshold_k"],
            "Limit_Margin": config["limit_margin"],
            "Moving_Average_Used": "No",
        })

    return pd.DataFrame(rows)


# ============================================================
# GENERATE ALL_RESULTS WITH WITHIN-LIMIT OUTLIERS
# ============================================================

def final_class_logic(row, config):
    if row["Outside_Limit_Flag"]:
        score = abs(row["Value_Z"])
        if pd.isna(score):
            return "Unknown"
        if score >= config["strong_anomaly_z"]:
            return "Strong Anomaly"
        if row["Persistent_Outside_Flag"]:
            if score >= config["drift_anomaly_z"]:
                return "Drift + Anomaly"
            return "Drift"
        # single outside-limit point is still an anomaly, but not a drift
        return "Drift + Anomaly"

    if row["Within_Limit_Spike_Flag"] or row["Within_Limit_Error_Change_Flag"]:
        return "Drift + Anomaly"

    if row["Persistent_InLimit_Deviation_Flag"]:
        return "Drift"

    return "Normal"


def outlier_type_logic(row):
    types = []
    if row["Outside_Limit_Flag"]:
        types.append("Outside Limit")
    if row["Within_Limit_Spike_Flag"]:
        types.append("Within Limit Spike")
    if row["Within_Limit_Error_Change_Flag"]:
        types.append("Within Limit Error Change")
    if row["Persistent_InLimit_Deviation_Flag"]:
        types.append("Within Limit Persistent Deviation")
    if not types:
        return "Normal"
    return " + ".join(types)


def generate_without_causal_all_results(df, tag_cols, timestamp_col, limits_df, config):
    out = []
    limits_map = limits_df.set_index("Tag").to_dict(orient="index")

    for tag in tag_cols:
        lim = limits_map[tag]
        temp = df[[timestamp_col, tag]].copy()
        temp.rename(columns={tag: "Actual_Value"}, inplace=True)
        temp["Tag"] = tag
        temp["Actual_Value"] = pd.to_numeric(temp["Actual_Value"], errors="coerce")

        ref_median = lim["Reference_Median"]
        ref_scale = lim["Reference_Scale_MAD"]
        if pd.isna(ref_scale) or ref_scale <= 0:
            ref_scale = config["std_epsilon"]

        lower = lim["Lower_Limit"]
        upper = lim["Upper_Limit"]
        central_lower = lim["Central_Lower"]
        central_upper = lim["Central_Upper"]

        temp["Reference_Mean"] = lim["Reference_Mean"]
        temp["Reference_Median"] = ref_median
        temp["Reference_Std"] = lim["Reference_Std"]
        temp["Reference_Scale_MAD"] = ref_scale
        temp["Lower_Limit"] = lower
        temp["Upper_Limit"] = upper
        temp["Central_Lower"] = central_lower
        temp["Central_Upper"] = central_upper

        temp["Error"] = temp["Actual_Value"] - ref_median
        temp["Value_Z"] = temp["Error"] / ref_scale
        temp["Abs_Value_Z"] = temp["Value_Z"].abs()

        temp["Delta"] = temp["Actual_Value"].diff()
        delta_scale = lim["Delta_Scale"] if not pd.isna(lim["Delta_Scale"]) else config["std_epsilon"]
        if delta_scale <= 0:
            delta_scale = config["std_epsilon"]
        temp["Delta_Z"] = (temp["Delta"].fillna(0) - lim["Delta_Center"]) / delta_scale
        temp["Abs_Delta_Z"] = temp["Delta_Z"].abs()

        temp["Error_Change"] = temp["Error"].diff()
        errchg_scale = lim["Error_Change_Scale"] if not pd.isna(lim["Error_Change_Scale"]) else config["std_epsilon"]
        if errchg_scale <= 0:
            errchg_scale = config["std_epsilon"]
        temp["Error_Change_Z"] = (temp["Error_Change"].fillna(0) - lim["Error_Change_Center"]) / errchg_scale
        temp["Abs_Error_Change_Z"] = temp["Error_Change_Z"].abs()

        abs_error_scale = lim["Abs_Error_Scale"] if not pd.isna(lim["Abs_Error_Scale"]) else config["std_epsilon"]
        if abs_error_scale <= 0:
            abs_error_scale = config["std_epsilon"]
        temp["Deviation_Level_Z"] = ((temp["Error"].abs()) - lim["Abs_Error_Center"]) / abs_error_scale

        temp["Value_Within_Limits"] = temp["Actual_Value"].between(lower, upper, inclusive="both")
        temp["Outside_Limit_Flag"] = ~temp["Value_Within_Limits"]

        temp["Inside_Central_Band"] = temp["Actual_Value"].between(central_lower, central_upper, inclusive="both")

        temp["Within_Limit_Spike_Flag"] = (
            temp["Value_Within_Limits"]
            & (temp["Abs_Delta_Z"] >= config["delta_spike_z"])
        )

        temp["Within_Limit_Error_Change_Flag"] = (
            temp["Value_Within_Limits"]
            & (temp["Abs_Error_Change_Z"] >= config["error_change_z"])
        )

        temp["Within_Limit_Deviation_Flag"] = (
            temp["Value_Within_Limits"]
            & (~temp["Inside_Central_Band"])
            & (temp["Deviation_Level_Z"] >= config["inlimit_deviation_z"])
        )

        temp["Outside_Run_Length"] = consecutive_run_lengths(temp["Outside_Limit_Flag"])
        temp["InLimit_Deviation_Run_Length"] = consecutive_run_lengths(temp["Within_Limit_Deviation_Flag"])

        temp["Persistent_Outside_Flag"] = temp["Outside_Run_Length"] >= config["outside_persistence_points"]
        temp["Persistent_InLimit_Deviation_Flag"] = (
            temp["InLimit_Deviation_Run_Length"] >= config["inlimit_deviation_persistence_points"]
        )

        temp["Limit_Status"] = np.select(
            [
                temp["Actual_Value"] < lower,
                temp["Actual_Value"] > upper,
                temp["Value_Within_Limits"],
            ],
            [
                "Below Lower Limit",
                "Above Upper Limit",
                "Within Limit",
            ],
            default="Unknown",
        )

        temp["Direction"] = np.select(
            [
                temp["Actual_Value"] < lower,
                temp["Actual_Value"] > upper,
                temp["Value_Within_Limits"] & (temp["Error"] < 0),
                temp["Value_Within_Limits"] & (temp["Error"] > 0),
            ],
            ["Down", "Up", "Within Limit - Down Deviation", "Within Limit - Up Deviation"],
            default="Normal",
        )

        temp["Outlier_Type"] = temp.apply(outlier_type_logic, axis=1)
        temp["Is_Outlier"] = temp["Outlier_Type"].ne("Normal")
        temp["Final_Class"] = temp.apply(lambda r: final_class_logic(r, config), axis=1)
        temp["Final_Status"] = temp["Final_Class"].apply(binary_status)

        # Keep causal-style fields blank because there is no causal prediction model.
        temp["Predicted_Value"] = np.nan
        temp["Residual"] = np.nan
        temp["Residual_Z"] = np.nan
        temp["Method"] = "Without Causal - Clean Anchored + Spike/Change + In-Limit Outliers, No Moving Average"

        temp = temp[
            [
                timestamp_col,
                "Tag",
                "Actual_Value",
                "Predicted_Value",
                "Residual",
                "Residual_Z",
                "Reference_Mean",
                "Reference_Median",
                "Reference_Std",
                "Reference_Scale_MAD",
                "Lower_Limit",
                "Upper_Limit",
                "Central_Lower",
                "Central_Upper",
                "Error",
                "Value_Z",
                "Abs_Value_Z",
                "Delta",
                "Delta_Z",
                "Abs_Delta_Z",
                "Error_Change",
                "Error_Change_Z",
                "Abs_Error_Change_Z",
                "Deviation_Level_Z",
                "Limit_Status",
                "Direction",
                "Value_Within_Limits",
                "Inside_Central_Band",
                "Outside_Limit_Flag",
                "Within_Limit_Spike_Flag",
                "Within_Limit_Error_Change_Flag",
                "Within_Limit_Deviation_Flag",
                "Outside_Run_Length",
                "InLimit_Deviation_Run_Length",
                "Persistent_Outside_Flag",
                "Persistent_InLimit_Deviation_Flag",
                "Outlier_Type",
                "Is_Outlier",
                "Final_Class",
                "Final_Status",
                "Method",
            ]
        ]
        out.append(temp)

    return pd.concat(out, ignore_index=True)


# ============================================================
# SUMMARIES
# ============================================================

def create_row_status(all_results, timestamp_col):
    return all_results.groupby(timestamp_col).agg(
        Total_Tags=("Tag", "count"),
        Abnormal_Tag_Count=("Final_Status", lambda x: (x == "Abnormal").sum()),
        Outside_Limit_Count=("Outside_Limit_Flag", "sum"),
        Within_Limit_Spike_Count=("Within_Limit_Spike_Flag", "sum"),
        Within_Limit_Error_Change_Count=("Within_Limit_Error_Change_Flag", "sum"),
        Within_Limit_Deviation_Count=("Within_Limit_Deviation_Flag", "sum"),
        Drift_Count=("Final_Class", lambda x: (x == "Drift").sum()),
        Drift_Anomaly_Count=("Final_Class", lambda x: (x == "Drift + Anomaly").sum()),
        Strong_Anomaly_Count=("Final_Class", lambda x: (x == "Strong Anomaly").sum()),
    ).reset_index().assign(
        Row_Final_Status=lambda d: np.where(d["Abnormal_Tag_Count"] > 0, "Abnormal", "Normal"),
        Abnormal_Tag_Rate=lambda d: d["Abnormal_Tag_Count"] / d["Total_Tags"],
    )


def create_tag_summary(all_results):
    tag_summary = all_results.groupby("Tag").agg(
        Total_Rows=("Final_Status", "count"),
        Normal_Count=("Final_Status", lambda x: (x == "Normal").sum()),
        Abnormal_Count=("Final_Status", lambda x: (x == "Abnormal").sum()),
        Outside_Limit_Count=("Outside_Limit_Flag", "sum"),
        Within_Limit_Spike_Count=("Within_Limit_Spike_Flag", "sum"),
        Within_Limit_Error_Change_Count=("Within_Limit_Error_Change_Flag", "sum"),
        Within_Limit_Deviation_Count=("Within_Limit_Deviation_Flag", "sum"),
        Drift_Count=("Final_Class", lambda x: (x == "Drift").sum()),
        Drift_Anomaly_Count=("Final_Class", lambda x: (x == "Drift + Anomaly").sum()),
        Strong_Anomaly_Count=("Final_Class", lambda x: (x == "Strong Anomaly").sum()),
    ).reset_index()
    tag_summary["Abnormal_Rate"] = tag_summary["Abnormal_Count"] / tag_summary["Total_Rows"]
    return tag_summary.sort_values("Abnormal_Rate", ascending=False)


def create_summary(df, tag_cols, clean_info, all_results, comparison=None):
    total = len(all_results)
    abnormal = int((all_results["Final_Status"] == "Abnormal").sum())
    outside = int(all_results["Outside_Limit_Flag"].sum())
    inlimit = int((all_results["Is_Outlier"] & all_results["Value_Within_Limits"]).sum())

    rows = [
        {"Metric": "Method", "Value": "Without causal - clean anchored + deviation spike/change + within-limit outlier"},
        {"Metric": "Moving Average Used", "Value": "No"},
        {"Metric": "Total Raw Rows", "Value": len(df)},
        {"Metric": "Total Tags", "Value": len(tag_cols)},
        {"Metric": "Total Tag-Timestamp Points", "Value": total},
        {"Metric": "Total Abnormal Points", "Value": abnormal},
        {"Metric": "Abnormal Rate", "Value": safe_divide(abnormal, total)},
        {"Metric": "Outside-Limit Outliers", "Value": outside},
        {"Metric": "Within-Limit Outliers", "Value": inlimit},
        {"Metric": "Clean Start Time", "Value": clean_info.loc[0, "Clean_Start_Time"]},
        {"Metric": "Clean End Time", "Value": clean_info.loc[0, "Clean_End_Time"]},
        {"Metric": "Clean Rows", "Value": clean_info.loc[0, "Clean_Rows"]},
        {"Metric": "Core Improvement", "Value": "Within-limit points can be abnormal if they show sudden deviation spike, error-change spike, or persistent central-band deviation."},
    ]

    if comparison is not None:
        for k, v in comparison["Binary_Summary"].iloc[0].to_dict().items():
            rows.append({"Metric": f"Comparison - {k}", "Value": v})

    return pd.DataFrame(rows)


# ============================================================
# BENCHMARK COMPARISON
# ============================================================

def load_benchmark_all_results(file_path, sheet_name="All_Results"):
    if file_path is None or str(file_path).strip() == "":
        return None
    if not os.path.exists(file_path):
        print(f"Benchmark file not found: {file_path}")
        return None

    xl = pd.ExcelFile(file_path)
    if sheet_name in xl.sheet_names:
        df = pd.read_excel(file_path, sheet_name=sheet_name)
    else:
        possible = [s for s in xl.sheet_names if "all" in s.lower() and "result" in s.lower()]
        if not possible:
            print("Benchmark All_Results sheet not found. Skipping comparison.")
            return None
        df = pd.read_excel(file_path, sheet_name=possible[0])
    return clean_column_names(df)


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
        "Timestamp", "Tag", "Actual_Value", "Lower_Limit", "Upper_Limit", "Central_Lower", "Central_Upper",
        "Outlier_Type", "Limit_Status", "Direction", "Final_Class", "Final_Status"
    ]].copy()
    wc_small.rename(columns={
        "Final_Class": "Without_Causal_Final_Class",
        "Final_Status": "Without_Causal_Final_Status",
    }, inplace=True)

    bench = standardize_benchmark_columns(benchmark_df)
    comp = wc_small.merge(bench, on=["Timestamp", "Tag"], how="inner")

    comp["Class_Match"] = (
        comp["Without_Causal_Final_Class"].astype(str).str.lower()
        == comp["Benchmark_Final_Class"].astype(str).str.lower()
    )
    comp["Binary_Match"] = (
        comp["Without_Causal_Final_Status"].astype(str).str.lower()
        == comp["Benchmark_Final_Status"].astype(str).str.lower()
    )

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

    tp = int(((comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Without_Causal_Final_Status"] == "Abnormal")).sum())
    tn = int(((comp["Benchmark_Final_Status"] == "Normal") & (comp["Without_Causal_Final_Status"] == "Normal")).sum())
    fp = int(((comp["Benchmark_Final_Status"] == "Normal") & (comp["Without_Causal_Final_Status"] == "Abnormal")).sum())
    fn = int(((comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Without_Causal_Final_Status"] == "Normal")).sum())
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

    class_comparison = pd.crosstab(
        comp["Benchmark_Final_Class"], comp["Without_Causal_Final_Class"], margins=True
    ).reset_index()
    binary_comparison = pd.crosstab(
        comp["Benchmark_Final_Status"], comp["Without_Causal_Final_Status"], margins=True
    ).reset_index()

    tag_rows = []
    for tag, g in comp.groupby("Tag"):
        tp_t = int(((g["Benchmark_Final_Status"] == "Abnormal") & (g["Without_Causal_Final_Status"] == "Abnormal")).sum())
        tn_t = int(((g["Benchmark_Final_Status"] == "Normal") & (g["Without_Causal_Final_Status"] == "Normal")).sum())
        fp_t = int(((g["Benchmark_Final_Status"] == "Normal") & (g["Without_Causal_Final_Status"] == "Abnormal")).sum())
        fn_t = int(((g["Benchmark_Final_Status"] == "Abnormal") & (g["Without_Causal_Final_Status"] == "Normal")).sum())
        tag_rows.append({
            "Tag": tag,
            "Total_Rows": len(g),
            "TP_Both_Abnormal": tp_t,
            "TN_Both_Normal": tn_t,
            "FP_Without_Causal_Only": fp_t,
            "FN_Benchmark_Only": fn_t,
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

    disagreements = comp[comp["Binary_Match"] == False].copy()

    return {
        "Binary_Summary": binary_summary,
        "Class_Comparison": class_comparison,
        "Binary_Comparison": binary_comparison,
        "Comparison_By_Tag": comparison_by_tag,
        "Comparison_By_Timestamp": comparison_by_timestamp,
        "Comparison_Row_Tag": comp,
        "Disagreements": disagreements,
    }


# ============================================================
# EXCEL WRITER
# ============================================================

def write_output_excel(output_file, sheets):
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            if df is None:
                continue
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    print(f"Excel file generated: {output_file}")


# ============================================================
# MAIN
# ============================================================

def main(config):
    print("Reading raw data...")
    df = read_excel_file(config["data_file"], config["data_sheet_name"])
    df = clean_column_names(df)

    timestamp_col = config["timestamp_col"]
    if timestamp_col not in df.columns:
        detected = find_column(df, ["Timestamp", "Time", "DateTime", "Date"])
        if detected is None:
            raise ValueError("Timestamp column not found.")
        timestamp_col = detected

    df[timestamp_col] = pd.to_datetime(df[timestamp_col], errors="coerce")
    df = df.dropna(subset=[timestamp_col]).sort_values(timestamp_col).reset_index(drop=True)

    tag_cols = [c for c in df.columns if c != timestamp_col]
    numeric_tag_cols = []
    for c in tag_cols:
        converted = pd.to_numeric(df[c], errors="coerce")
        if converted.notna().sum() > 0:
            df[c] = converted
            numeric_tag_cols.append(c)
    tag_cols = numeric_tag_cols

    if not tag_cols:
        raise ValueError("No numeric tag columns found in raw data.")

    print(f"Rows: {len(df)}")
    print(f"Numeric tags: {len(tag_cols)}")
    print("Detecting clean period without moving average...")
    clean_df, clean_info, clean_score = detect_clean_period_no_mavg(df, tag_cols, timestamp_col, config)

    print("Building clean-like limits...")
    limits_df = build_clean_like_limits(df, clean_df, clean_score, tag_cols, timestamp_col, config)

    print("Generating All_Results with outside-limit and within-limit outliers...")
    all_results = generate_without_causal_all_results(df, tag_cols, timestamp_col, limits_df, config)

    row_status = create_row_status(all_results, timestamp_col)
    tag_summary = create_tag_summary(all_results)

    status_mapping = pd.DataFrame([
        {"Condition": "Outside lower/upper limit", "Outlier_Type": "Outside Limit", "Final_Class": "Drift / Drift + Anomaly / Strong Anomaly", "Final_Status": "Abnormal"},
        {"Condition": "Within limit but sudden Delta_Z spike", "Outlier_Type": "Within Limit Spike", "Final_Class": "Drift + Anomaly", "Final_Status": "Abnormal"},
        {"Condition": "Within limit but sudden Error_Change_Z spike", "Outlier_Type": "Within Limit Error Change", "Final_Class": "Drift + Anomaly", "Final_Status": "Abnormal"},
        {"Condition": "Within limit but outside central band with persistent deviation", "Outlier_Type": "Within Limit Persistent Deviation", "Final_Class": "Drift", "Final_Status": "Abnormal"},
        {"Condition": "Within broad limit and no spike/change/deviation", "Outlier_Type": "Normal", "Final_Class": "Normal", "Final_Status": "Normal"},
        {"Condition": "Moving average / rolling average", "Outlier_Type": "Not Used", "Final_Class": "Not Used", "Final_Status": "Not Used"},
    ])

    benchmark_df = load_benchmark_all_results(config["benchmark_result_file"], config["benchmark_sheet_name"])
    comparison = None
    if benchmark_df is not None:
        print("Comparing with benchmark...")
        comparison = compare_results(all_results, benchmark_df, timestamp_col)
    else:
        print("Benchmark comparison skipped.")

    summary = create_summary(df, tag_cols, clean_info, all_results, comparison)

    sheets = {
        "Summary": summary,
        "Status_Mapping": status_mapping,
        "Clean_Period_Info": clean_info,
        "Clean_Score_By_Row": clean_score,
        "Clean_Like_Limits": limits_df,
        "Without_Causal_All_Results": all_results,
        "Row_Status": row_status,
        "Tag_Summary": tag_summary,
    }

    if comparison is not None:
        sheets.update({
            "Binary_Summary": comparison["Binary_Summary"],
            "Class_Comparison": comparison["Class_Comparison"],
            "Binary_Comparison": comparison["Binary_Comparison"],
            "Comparison_By_Tag": comparison["Comparison_By_Tag"],
            "Comparison_By_Timestamp": comparison["Comparison_By_Timestamp"],
            "Comparison_Row_Tag": comparison["Comparison_Row_Tag"],
            "Disagreements": comparison["Disagreements"],
        })

    print("Writing Excel output...")
    write_output_excel(config["output_file"], sheets)
    print("Completed.")

    return {
        "summary": summary,
        "clean_info": clean_info,
        "limits": limits_df,
        "all_results": all_results,
        "comparison": comparison,
    }


# ============================================================
# CLI
# ============================================================

def parse_args():
    p = argparse.ArgumentParser(description="Without-causal outlier detection without moving average, including within-limit outliers.")
    p.add_argument("--data_file", type=str, default=CONFIG["data_file"])
    p.add_argument("--benchmark_file", type=str, default=CONFIG["benchmark_result_file"])
    p.add_argument("--output_file", type=str, default=CONFIG["output_file"])
    p.add_argument("--data_sheet_name", type=str, default=None)
    p.add_argument("--benchmark_sheet_name", type=str, default=CONFIG["benchmark_sheet_name"])
    p.add_argument("--timestamp_col", type=str, default=CONFIG["timestamp_col"])

    p.add_argument("--candidate_z_limit", type=float, default=CONFIG["candidate_z_limit"])
    p.add_argument("--threshold_k", type=float, default=CONFIG["threshold_k"])
    p.add_argument("--limit_margin", type=float, default=CONFIG["limit_margin"])
    p.add_argument("--delta_spike_z", type=float, default=CONFIG["delta_spike_z"])
    p.add_argument("--error_change_z", type=float, default=CONFIG["error_change_z"])
    p.add_argument("--inlimit_deviation_z", type=float, default=CONFIG["inlimit_deviation_z"])
    p.add_argument("--outside_persistence_points", type=int, default=CONFIG["outside_persistence_points"])
    p.add_argument("--inlimit_deviation_persistence_points", type=int, default=CONFIG["inlimit_deviation_persistence_points"])

    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    CONFIG["data_file"] = args.data_file
    CONFIG["benchmark_result_file"] = args.benchmark_file
    CONFIG["output_file"] = args.output_file
    CONFIG["data_sheet_name"] = args.data_sheet_name
    CONFIG["benchmark_sheet_name"] = args.benchmark_sheet_name
    CONFIG["timestamp_col"] = args.timestamp_col

    CONFIG["candidate_z_limit"] = args.candidate_z_limit
    CONFIG["threshold_k"] = args.threshold_k
    CONFIG["limit_margin"] = args.limit_margin
    CONFIG["delta_spike_z"] = args.delta_spike_z
    CONFIG["error_change_z"] = args.error_change_z
    CONFIG["inlimit_deviation_z"] = args.inlimit_deviation_z
    CONFIG["outside_persistence_points"] = args.outside_persistence_points
    CONFIG["inlimit_deviation_persistence_points"] = args.inlimit_deviation_persistence_points

    main(CONFIG)
