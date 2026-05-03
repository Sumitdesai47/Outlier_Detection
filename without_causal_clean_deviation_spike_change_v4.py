"""
WITHOUT-CAUSAL OUTLIER DETECTION - CLEAN ANCHORED + DEVIATION SPIKE/CHANGE LOGIC

Purpose
-------
This script improves clean-data-limit outlier detection by adding checks for:
  1) Deviation level
  2) Deviation spike
  3) Deviation/error change
  4) Persistence

Why this version is better
--------------------------
Old issue 1:
    One clean period may be biased high/low for some tags.
Fix:
    Use the clean window as an anchor, then add clean-like historical rows per tag.

Old issue 2:
    Full-data thresholds become too wide and only very high points are detected.
Fix:
    Do NOT use full data directly. Use clean-like candidate rows only.

Old issue 3:
    Similar later periods are marked as drift.
Fix:
    If later values are close to the clean-anchor distribution and have no spike/change,
    they are included in the tag-wise reference population and marked Normal.

Run
---
python without_causal_clean_deviation_spike_change_v4.py \
    --data_file "Multi_X_Multi_Y_Correct_Data.xlsx" \
    --benchmark_file "context_aware_outlier_results(1).xlsx" \
    --output_file "without_causal_clean_deviation_spike_change_v4_result.xlsx"

Required packages
-----------------
pip install pandas numpy openpyxl
"""

import os
import argparse
import numpy as np
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

CONFIG = {
    "data_file": "Multi_X_Multi_Y_Correct_Data.xlsx",
    "benchmark_result_file": "",
    "data_sheet_name": None,
    "benchmark_sheet_name": "All_Results",
    "timestamp_col": "Timestamp",
    "output_file": "without_causal_clean_deviation_spike_change_v4_result.xlsx",

    # Clean window selection
    "clean_window_rows": None,          # None = auto: 15% rows, min 30, max 200
    "level_z_limit": 3.5,               # row-level deviation check
    "spike_z_limit": 4.0,               # local spike check
    "change_z_limit": 4.0,              # deviation/error change check
    "volatility_z_limit": 4.0,          # rolling volatility check

    # Clean score weights
    "w_level": 0.35,
    "w_spike": 0.25,
    "w_change": 0.25,
    "w_volatility": 0.10,
    "w_missing": 0.05,

    # Tag-wise clean-like reference expansion
    "candidate_z_limit": 2.75,          # include rows similar to clean anchor
    "max_row_clean_score_quantile": 0.60,
    "min_reference_rows": 30,

    # Final clean limits
    "threshold_k": 3.5,                 # median +/- K * robust scale
    "limit_margin": 0.12,               # expand limits slightly

    # Persistence logic
    "persistence_window": 3,
    "persistence_min_points": 2,

    # Final class thresholds
    "drift_z": 3.0,
    "drift_anomaly_z": 3.75,
    "strong_anomaly_z": 5.0,

    "std_epsilon": 1e-9,
}


# ============================================================
# BASIC HELPERS
# ============================================================

def read_excel_file(file_path, sheet_name=None):
    if sheet_name is None:
        return pd.read_excel(file_path)
    return pd.read_excel(file_path, sheet_name=sheet_name)


def clean_column_names(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def find_column(df, possible_names):
    col_map = {str(c).strip().lower(): c for c in df.columns}

    for name in possible_names:
        if name.lower() in col_map:
            return col_map[name.lower()]

    for c in df.columns:
        c_norm = str(c).strip().lower().replace(" ", "_")
        for name in possible_names:
            name_norm = name.lower().replace(" ", "_")
            if c_norm == name_norm:
                return c

    return None


def safe_divide(a, b):
    if b == 0 or pd.isna(b):
        return np.nan
    return a / b


def robust_location_scale(series, std_epsilon=1e-9):
    s = pd.to_numeric(series, errors="coerce").dropna()

    if len(s) == 0:
        return np.nan, np.nan

    median = s.median()
    mad = (s - median).abs().median()
    scale = 1.4826 * mad

    if pd.isna(scale) or scale < std_epsilon:
        scale = s.std()

    if pd.isna(scale) or scale < std_epsilon:
        scale = std_epsilon

    return median, scale


def robust_z_series(series, center=None, scale=None, std_epsilon=1e-9):
    s = pd.to_numeric(series, errors="coerce")

    if center is None or scale is None:
        center, scale = robust_location_scale(s, std_epsilon)

    if pd.isna(scale) or scale < std_epsilon:
        scale = std_epsilon

    return (s - center) / scale


def binary_status(final_class):
    if pd.isna(final_class):
        return "Unknown"

    fc = str(final_class).strip().lower()

    if fc in ["normal", "ok", "good"]:
        return "Normal"
    if fc in ["unknown", "", "nan", "none"]:
        return "Unknown"
    return "Abnormal"


def classify_final(row, config):
    """
    Final class logic using clean limit + spike/change/persistence.
    """
    if pd.isna(row["Actual_Value"]):
        return "Unknown"

    if not row["Is_Level_Outlier"]:
        return "Normal"

    abs_z = row["Abs_Value_Z"]

    # Very extreme level deviation or very strong spike/change
    if (
        abs_z >= config["strong_anomaly_z"]
        or abs(row["Deviation_Spike_Z"]) >= config["strong_anomaly_z"]
        or abs(row["Deviation_Change_Z"]) >= config["strong_anomaly_z"]
    ):
        return "Strong Anomaly"

    # Isolated outlier with spike/change = anomaly-like event
    if not row["Persistent_Level_Outlier"]:
        if row["Deviation_Spike_Flag"] or row["Deviation_Change_Flag"]:
            return "Drift + Anomaly"
        # isolated low-confidence outlier: avoid false drift
        return "Normal"

    # Persistent level shift
    if abs_z < config["drift_anomaly_z"]:
        return "Drift"

    # Persistent level shift with additional instability
    if row["Deviation_Spike_Flag"] or row["Deviation_Change_Flag"]:
        return "Drift + Anomaly"

    if abs_z >= config["drift_anomaly_z"]:
        return "Drift + Anomaly"

    return "Drift"


# ============================================================
# FEATURE ENGINEERING: DEVIATION, SPIKE, CHANGE
# ============================================================

def compute_tag_deviation_features(df, tag_cols, config):
    """
    Creates tag-level feature dictionaries:
      - level_z: robust deviation from historical median
      - spike_z: local spike score using rolling median residual
      - change_z: change in deviation/error score
      - volatility_z: rolling volatility of deviation

    These are used for clean-window selection and final classification.
    """

    features = {}

    for tag in tag_cols:
        x = pd.to_numeric(df[tag], errors="coerce")

        # 1) Level deviation from robust historical center
        global_median, global_scale = robust_location_scale(x, config["std_epsilon"])
        level_z = robust_z_series(x, global_median, global_scale, config["std_epsilon"])

        # 2) Local spike:
        # Compare actual value with rolling median. This catches sudden point spikes.
        rolling_med = x.rolling(window=5, center=True, min_periods=2).median()
        local_residual = x - rolling_med
        _, local_resid_scale = robust_location_scale(local_residual, config["std_epsilon"])
        spike_z = local_residual / local_resid_scale

        # 3) Deviation/error change:
        # Difference of deviation score. This catches sudden change in behavior.
        deviation_change = level_z.diff()
        _, change_scale = robust_location_scale(deviation_change, config["std_epsilon"])
        change_z = deviation_change / change_scale

        # 4) Rolling volatility of deviation:
        rolling_vol = level_z.rolling(window=7, center=True, min_periods=3).std()
        vol_center, vol_scale = robust_location_scale(rolling_vol, config["std_epsilon"])
        volatility_z = (rolling_vol - vol_center) / vol_scale

        features[tag] = pd.DataFrame({
            "Level_Z": level_z,
            "Deviation_Spike_Z": spike_z.fillna(0),
            "Deviation_Change_Z": change_z.fillna(0),
            "Deviation_Volatility_Z": volatility_z.fillna(0),
        })

    return features


# ============================================================
# CLEAN WINDOW SELECTION
# ============================================================

def build_clean_score(df, tag_cols, tag_features, config):
    """
    Row-level clean score using:
      - level deviation fraction
      - spike fraction
      - deviation/error change fraction
      - volatility fraction
      - missing fraction
    """

    n = len(df)
    level_bad = np.zeros(n)
    spike_bad = np.zeros(n)
    change_bad = np.zeros(n)
    volatility_bad = np.zeros(n)
    missing_bad = np.zeros(n)

    for tag in tag_cols:
        x = pd.to_numeric(df[tag], errors="coerce")
        f = tag_features[tag]

        level_bad += (f["Level_Z"].abs() > config["level_z_limit"]).astype(int).values
        spike_bad += (f["Deviation_Spike_Z"].abs() > config["spike_z_limit"]).astype(int).values
        change_bad += (f["Deviation_Change_Z"].abs() > config["change_z_limit"]).astype(int).values
        volatility_bad += (f["Deviation_Volatility_Z"].abs() > config["volatility_z_limit"]).astype(int).values
        missing_bad += x.isna().astype(int).values

    tag_count = max(len(tag_cols), 1)

    level_frac = level_bad / tag_count
    spike_frac = spike_bad / tag_count
    change_frac = change_bad / tag_count
    volatility_frac = volatility_bad / tag_count
    missing_frac = missing_bad / tag_count

    clean_score = (
        config["w_level"] * level_frac
        + config["w_spike"] * spike_frac
        + config["w_change"] * change_frac
        + config["w_volatility"] * volatility_frac
        + config["w_missing"] * missing_frac
    )

    return pd.DataFrame({
        "Level_Bad_Fraction": level_frac,
        "Spike_Bad_Fraction": spike_frac,
        "Deviation_Change_Bad_Fraction": change_frac,
        "Volatility_Bad_Fraction": volatility_frac,
        "Missing_Fraction": missing_frac,
        "Clean_Score": clean_score,
    })


def detect_clean_window(df, tag_cols, timestamp_col, tag_features, config):
    n_rows = len(df)

    if config["clean_window_rows"] is None:
        window = max(30, min(200, int(n_rows * 0.15)))
    else:
        window = int(config["clean_window_rows"])

    if window >= n_rows:
        window = max(5, int(n_rows * 0.30))

    score_df = build_clean_score(df, tag_cols, tag_features, config)
    score_df.insert(0, timestamp_col, df[timestamp_col].values)

    rolling_score = score_df["Clean_Score"].rolling(window=window, min_periods=window).mean()
    best_end_idx = rolling_score.idxmin()

    if pd.isna(best_end_idx):
        raise ValueError("Clean period could not be detected. Reduce clean_window_rows.")

    best_start_idx = int(best_end_idx) - window + 1
    best_end_idx = int(best_end_idx)

    clean_info = pd.DataFrame([{
        "Clean_Start_Index": best_start_idx,
        "Clean_End_Index": best_end_idx,
        "Clean_Start_Time": df.loc[best_start_idx, timestamp_col],
        "Clean_End_Time": df.loc[best_end_idx, timestamp_col],
        "Clean_Rows": window,
        "Clean_Window_Avg_Score": score_df.loc[best_start_idx:best_end_idx, "Clean_Score"].mean(),
        "Clean_Window_Avg_Level_Bad_Fraction": score_df.loc[best_start_idx:best_end_idx, "Level_Bad_Fraction"].mean(),
        "Clean_Window_Avg_Spike_Bad_Fraction": score_df.loc[best_start_idx:best_end_idx, "Spike_Bad_Fraction"].mean(),
        "Clean_Window_Avg_Deviation_Change_Bad_Fraction": score_df.loc[best_start_idx:best_end_idx, "Deviation_Change_Bad_Fraction"].mean(),
        "Clean_Window_Avg_Volatility_Bad_Fraction": score_df.loc[best_start_idx:best_end_idx, "Volatility_Bad_Fraction"].mean(),
        "Clean_Method": "Lowest rolling clean score using level deviation + spike + deviation/error change + volatility",
    }])

    clean_df = df.iloc[best_start_idx:best_end_idx + 1].copy()

    return clean_df, clean_info, score_df


# ============================================================
# TAG-WISE CLEAN-LIKE REFERENCE POPULATION
# ============================================================

def build_reference_limits(df, clean_df, tag_cols, tag_features, clean_score_df, config):
    """
    For each tag:
      1. Use detected clean window as anchor.
      2. Find all rows similar to the clean anchor for that tag.
      3. Exclude rows with spike/change/dirty global row score.
      4. Calculate robust limits from selected reference population.
    """

    max_clean_score = clean_score_df["Clean_Score"].quantile(config["max_row_clean_score_quantile"])

    rows = []
    reference_masks = {}

    for tag in tag_cols:
        x_all = pd.to_numeric(df[tag], errors="coerce")
        x_clean = pd.to_numeric(clean_df[tag], errors="coerce").dropna()

        if len(x_clean) == 0:
            continue

        anchor_median, anchor_scale = robust_location_scale(x_clean, config["std_epsilon"])
        anchor_z = (x_all - anchor_median) / anchor_scale

        f = tag_features[tag]

        # Similar to clean anchor + no instability
        candidate_mask = (
            anchor_z.abs().le(config["candidate_z_limit"])
            & f["Deviation_Spike_Z"].abs().le(config["spike_z_limit"])
            & f["Deviation_Change_Z"].abs().le(config["change_z_limit"])
            & clean_score_df["Clean_Score"].le(max_clean_score)
            & x_all.notna()
        )

        # Always include selected clean window rows, even if global score filter is strict
        clean_indices = clean_df.index
        candidate_mask.loc[clean_indices] = x_all.loc[clean_indices].notna()

        reference_values = x_all[candidate_mask].dropna()

        # Fallback: if not enough points, use clean window only
        if len(reference_values) < config["min_reference_rows"]:
            reference_values = x_clean.copy()
            candidate_mask = pd.Series(False, index=df.index)
            candidate_mask.loc[clean_indices] = x_all.loc[clean_indices].notna()

        ref_mean = reference_values.mean()
        ref_median, ref_scale = robust_location_scale(reference_values, config["std_epsilon"])
        ref_std = reference_values.std()
        ref_q01 = reference_values.quantile(0.01)
        ref_q05 = reference_values.quantile(0.05)
        ref_q95 = reference_values.quantile(0.95)
        ref_q99 = reference_values.quantile(0.99)

        lower = ref_median - config["threshold_k"] * ref_scale
        upper = ref_median + config["threshold_k"] * ref_scale

        # Ensure reference population itself is mostly inside limits
        lower = min(lower, ref_q01)
        upper = max(upper, ref_q99)

        width = upper - lower
        if pd.isna(width) or width <= config["std_epsilon"]:
            width = max(ref_scale, config["std_epsilon"])

        lower = lower - config["limit_margin"] * width
        upper = upper + config["limit_margin"] * width

        rows.append({
            "Tag": tag,
            "Clean_Anchor_Count": len(x_clean),
            "Reference_Count": len(reference_values),
            "Reference_Source": "Clean anchor + clean-like rows with no spike/change",
            "Anchor_Median": anchor_median,
            "Anchor_Scale": anchor_scale,
            "Reference_Mean": ref_mean,
            "Reference_Median": ref_median,
            "Reference_Robust_Scale": ref_scale,
            "Reference_Std": ref_std,
            "Reference_Q01": ref_q01,
            "Reference_Q05": ref_q05,
            "Reference_Q95": ref_q95,
            "Reference_Q99": ref_q99,
            "Threshold_K": config["threshold_k"],
            "Candidate_Z_Limit": config["candidate_z_limit"],
            "Lower_Limit": lower,
            "Upper_Limit": upper,
        })

        reference_masks[tag] = candidate_mask

    limits_df = pd.DataFrame(rows)
    return limits_df, reference_masks


# ============================================================
# GENERATE ALL_RESULTS
# ============================================================

def generate_without_causal_results(df, tag_cols, timestamp_col, limits_df, reference_masks, tag_features, config):
    limit_map = limits_df.set_index("Tag").to_dict(orient="index")
    output = []

    for tag in tag_cols:
        if tag not in limit_map:
            continue

        lim = limit_map[tag]
        x = pd.to_numeric(df[tag], errors="coerce")
        f = tag_features[tag]

        lower = lim["Lower_Limit"]
        upper = lim["Upper_Limit"]
        ref_median = lim["Reference_Median"]
        ref_scale = lim["Reference_Robust_Scale"]
        ref_mean = lim["Reference_Mean"]
        ref_std = lim["Reference_Std"]

        temp = pd.DataFrame({
            timestamp_col: df[timestamp_col],
            "Tag": tag,
            "Actual_Value": x,
            "Predicted_Value": np.nan,
            "Residual": np.nan,
            "Residual_Z": np.nan,
            "Reference_Mean": ref_mean,
            "Reference_Std": ref_std,
            "Reference_Median": ref_median,
            "Reference_Robust_Scale": ref_scale,
            "Lower_Limit": lower,
            "Upper_Limit": upper,
        })

        temp["Value_Z"] = (temp["Actual_Value"] - ref_median) / ref_scale
        temp["Abs_Value_Z"] = temp["Value_Z"].abs()

        temp["Deviation_Level_Z"] = f["Level_Z"].values
        temp["Deviation_Spike_Z"] = f["Deviation_Spike_Z"].values
        temp["Deviation_Change_Z"] = f["Deviation_Change_Z"].values
        temp["Deviation_Volatility_Z"] = f["Deviation_Volatility_Z"].values

        temp["Is_Reference_Candidate"] = reference_masks[tag].values
        temp["Is_Level_Outlier"] = (temp["Actual_Value"] < lower) | (temp["Actual_Value"] > upper)
        temp["Deviation_Spike_Flag"] = temp["Deviation_Spike_Z"].abs() > config["spike_z_limit"]
        temp["Deviation_Change_Flag"] = temp["Deviation_Change_Z"].abs() > config["change_z_limit"]
        temp["Volatility_Flag"] = temp["Deviation_Volatility_Z"].abs() > config["volatility_z_limit"]

        outlier_int = temp["Is_Level_Outlier"].astype(int)
        temp["Level_Outlier_Count_In_Window"] = (
            outlier_int
            .rolling(window=config["persistence_window"], min_periods=1, center=True)
            .sum()
        )

        temp["Persistent_Level_Outlier"] = temp["Level_Outlier_Count_In_Window"] >= config["persistence_min_points"]

        temp["Limit_Status"] = np.select(
            [
                temp["Actual_Value"] < lower,
                temp["Actual_Value"] > upper,
            ],
            [
                "Below Lower Limit",
                "Above Upper Limit",
            ],
            default="Within Limit"
        )

        temp["Direction"] = np.select(
            [
                temp["Actual_Value"] < lower,
                temp["Actual_Value"] > upper,
            ],
            [
                "Down",
                "Up",
            ],
            default="Normal"
        )

        temp["Final_Class"] = temp.apply(lambda r: classify_final(r, config), axis=1)
        temp["Final_Status"] = temp["Final_Class"].apply(binary_status)
        temp["Method"] = "Without Causal - Clean Anchor + Deviation Spike/Change"

        temp = temp[[
            timestamp_col,
            "Tag",
            "Actual_Value",
            "Predicted_Value",
            "Residual",
            "Residual_Z",
            "Reference_Mean",
            "Reference_Std",
            "Reference_Median",
            "Reference_Robust_Scale",
            "Lower_Limit",
            "Upper_Limit",
            "Value_Z",
            "Abs_Value_Z",
            "Deviation_Level_Z",
            "Deviation_Spike_Z",
            "Deviation_Change_Z",
            "Deviation_Volatility_Z",
            "Is_Reference_Candidate",
            "Is_Level_Outlier",
            "Deviation_Spike_Flag",
            "Deviation_Change_Flag",
            "Volatility_Flag",
            "Level_Outlier_Count_In_Window",
            "Persistent_Level_Outlier",
            "Limit_Status",
            "Direction",
            "Final_Class",
            "Final_Status",
            "Method",
        ]]

        output.append(temp)

    return pd.concat(output, ignore_index=True)


# ============================================================
# SUMMARIES
# ============================================================

def create_row_status(all_results, timestamp_col):
    row_status = all_results.groupby(timestamp_col).agg(
        Total_Tags=("Tag", "count"),
        Abnormal_Tag_Count=("Final_Status", lambda x: (x == "Abnormal").sum()),
        Drift_Count=("Final_Class", lambda x: (x == "Drift").sum()),
        Drift_Anomaly_Count=("Final_Class", lambda x: (x == "Drift + Anomaly").sum()),
        Strong_Anomaly_Count=("Final_Class", lambda x: (x == "Strong Anomaly").sum()),
        Spike_Flag_Count=("Deviation_Spike_Flag", "sum"),
        Change_Flag_Count=("Deviation_Change_Flag", "sum"),
        Volatility_Flag_Count=("Volatility_Flag", "sum"),
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
        Spike_Flag_Count=("Deviation_Spike_Flag", "sum"),
        Change_Flag_Count=("Deviation_Change_Flag", "sum"),
        Volatility_Flag_Count=("Volatility_Flag", "sum"),
        Reference_Candidate_Count=("Is_Reference_Candidate", "sum"),
    ).reset_index()

    tag_summary["Abnormal_Rate"] = tag_summary["Abnormal_Count"] / tag_summary["Total_Rows"]
    tag_summary["Reference_Candidate_Rate"] = tag_summary["Reference_Candidate_Count"] / tag_summary["Total_Rows"]

    return tag_summary.sort_values("Abnormal_Rate", ascending=False)


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
        bench = pd.read_excel(file_path, sheet_name=sheet_name)
    else:
        possible_sheet = None
        for s in xl.sheet_names:
            if "all" in s.lower() and "result" in s.lower():
                possible_sheet = s
                break

        if possible_sheet is None:
            print("Benchmark All_Results sheet not found. Skipping comparison.")
            return None

        bench = pd.read_excel(file_path, sheet_name=possible_sheet)

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
    out = out.dropna(subset=["Timestamp", "Tag"])

    return out


def compare_results(all_results, benchmark_df, timestamp_col):
    wc = all_results.copy()
    wc = wc.rename(columns={timestamp_col: "Timestamp"})
    wc["Timestamp"] = pd.to_datetime(wc["Timestamp"], errors="coerce")
    wc["Tag"] = wc["Tag"].astype(str).str.strip()

    wc_small = wc[[
        "Timestamp",
        "Tag",
        "Actual_Value",
        "Lower_Limit",
        "Upper_Limit",
        "Value_Z",
        "Deviation_Spike_Z",
        "Deviation_Change_Z",
        "Limit_Status",
        "Direction",
        "Final_Class",
        "Final_Status",
    ]].copy()

    wc_small.rename(columns={
        "Final_Class": "Without_Causal_Final_Class",
        "Final_Status": "Without_Causal_Final_Status",
    }, inplace=True)

    bench_std = standardize_benchmark_columns(benchmark_df)

    comp = wc_small.merge(bench_std, on=["Timestamp", "Tag"], how="inner")

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
        [
            "Both Abnormal",
            "Both Normal",
            "Benchmark Only Abnormal",
            "Without Causal Only Abnormal",
        ],
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
        "Exact_Final_Class_Match": comp["Class_Match"].mean() if total > 0 else np.nan,
    }])

    class_comparison = pd.crosstab(
        comp["Benchmark_Final_Class"],
        comp["Without_Causal_Final_Class"],
        margins=True,
    ).reset_index()

    binary_comparison = pd.crosstab(
        comp["Benchmark_Final_Status"],
        comp["Without_Causal_Final_Status"],
        margins=True,
    ).reset_index()

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
# SUMMARY AND EXCEL WRITER
# ============================================================

def create_summary(df, tag_cols, clean_info, limits_df, all_results, row_status, comparison_outputs, config):
    total_points = len(all_results)
    abnormal_points = int((all_results["Final_Status"] == "Abnormal").sum())
    normal_points = int((all_results["Final_Status"] == "Normal").sum())

    rows = [
        {"Metric": "Method", "Value": "Without causal - clean anchored + deviation spike/change logic"},
        {"Metric": "Total Raw Rows", "Value": len(df)},
        {"Metric": "Total Tags", "Value": len(tag_cols)},
        {"Metric": "Total Tag-Timestamp Points", "Value": total_points},
        {"Metric": "Normal Points", "Value": normal_points},
        {"Metric": "Abnormal Points", "Value": abnormal_points},
        {"Metric": "Abnormal Rate", "Value": safe_divide(abnormal_points, total_points)},
        {"Metric": "Clean Start Time", "Value": clean_info.loc[0, "Clean_Start_Time"]},
        {"Metric": "Clean End Time", "Value": clean_info.loc[0, "Clean_End_Time"]},
        {"Metric": "Clean Rows", "Value": clean_info.loc[0, "Clean_Rows"]},
        {"Metric": "Clean Logic", "Value": "Lowest rolling score using deviation level, deviation spike, deviation/error change, volatility, and missing values"},
        {"Metric": "Reference Logic", "Value": "Clean anchor plus tag-wise clean-like rows without spike/change"},
        {"Metric": "Final Logic", "Value": "Outlier must be outside clean-like limits and persistent, unless strong spike/change"},
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


def write_output_excel(output_file, sheets):
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        for name, data in sheets.items():
            if data is None:
                continue
            data.to_excel(writer, sheet_name=name[:31], index=False)

    print(f"Excel generated: {output_file}")


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
    df = df.dropna(subset=[timestamp_col]).copy()
    df = df.sort_values(timestamp_col).reset_index(drop=True)

    tag_cols = [c for c in df.columns if c != timestamp_col]

    numeric_tag_cols = []
    for c in tag_cols:
        converted = pd.to_numeric(df[c], errors="coerce")
        if converted.notna().sum() > 0:
            df[c] = converted
            numeric_tag_cols.append(c)

    tag_cols = numeric_tag_cols

    if len(tag_cols) == 0:
        raise ValueError("No numeric tag columns found.")

    print(f"Rows: {len(df)}")
    print(f"Numeric tags: {len(tag_cols)}")

    print("Computing deviation level, spike, and change features...")
    tag_features = compute_tag_deviation_features(df, tag_cols, config)

    print("Detecting clean window using deviation + spike + change score...")
    clean_df, clean_info, clean_score_df = detect_clean_window(df, tag_cols, timestamp_col, tag_features, config)

    print("Building tag-wise clean-like reference limits...")
    limits_df, reference_masks = build_reference_limits(df, clean_df, tag_cols, tag_features, clean_score_df, config)

    print("Generating without-causal All_Results format...")
    all_results = generate_without_causal_results(df, tag_cols, timestamp_col, limits_df, reference_masks, tag_features, config)

    print("Creating row/tag summaries...")
    row_status = create_row_status(all_results, timestamp_col)
    tag_summary = create_tag_summary(all_results)

    status_mapping = pd.DataFrame([
        {
            "Rule": "Inside clean-like lower/upper limits",
            "Final_Class": "Normal",
            "Final_Status": "Normal",
        },
        {
            "Rule": "Outside limits but isolated and no spike/change",
            "Final_Class": "Normal",
            "Final_Status": "Normal",
        },
        {
            "Rule": "Outside limits + persistent + moderate deviation",
            "Final_Class": "Drift",
            "Final_Status": "Abnormal",
        },
        {
            "Rule": "Outside limits + persistent + high deviation or spike/change",
            "Final_Class": "Drift + Anomaly",
            "Final_Status": "Abnormal",
        },
        {
            "Rule": "Very strong deviation OR very strong spike/change",
            "Final_Class": "Strong Anomaly",
            "Final_Status": "Abnormal",
        },
    ])

    print("Checking benchmark comparison...")
    benchmark_df = load_benchmark_all_results(config["benchmark_result_file"], config["benchmark_sheet_name"])

    comparison_outputs = None
    if benchmark_df is not None:
        print("Comparing with benchmark/context-aware result...")
        comparison_outputs = compare_results(all_results, benchmark_df, timestamp_col)
    else:
        print("Benchmark comparison skipped.")

    summary = create_summary(
        df=df,
        tag_cols=tag_cols,
        clean_info=clean_info,
        limits_df=limits_df,
        all_results=all_results,
        row_status=row_status,
        comparison_outputs=comparison_outputs,
        config=config,
    )

    sheets = {
        "Summary": summary,
        "Status_Mapping": status_mapping,
        "Clean_Period_Info": clean_info,
        "Clean_Score_By_Row": clean_score_df,
        "Clean_Like_Limits": limits_df,
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
    write_output_excel(config["output_file"], sheets)

    print("Completed.")
    return {
        "summary": summary,
        "clean_info": clean_info,
        "limits_df": limits_df,
        "all_results": all_results,
        "row_status": row_status,
        "tag_summary": tag_summary,
        "comparison_outputs": comparison_outputs,
    }


# ============================================================
# CLI
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Without causal outlier detection using clean anchor + deviation spike/change logic."
    )

    parser.add_argument("--data_file", type=str, default=CONFIG["data_file"])
    parser.add_argument("--benchmark_file", type=str, default=CONFIG["benchmark_result_file"])
    parser.add_argument("--output_file", type=str, default=CONFIG["output_file"])
    parser.add_argument("--data_sheet_name", type=str, default=None)
    parser.add_argument("--benchmark_sheet_name", type=str, default=CONFIG["benchmark_sheet_name"])
    parser.add_argument("--timestamp_col", type=str, default=CONFIG["timestamp_col"])

    parser.add_argument("--clean_window_rows", type=int, default=None)
    parser.add_argument("--candidate_z_limit", type=float, default=CONFIG["candidate_z_limit"])
    parser.add_argument("--threshold_k", type=float, default=CONFIG["threshold_k"])
    parser.add_argument("--limit_margin", type=float, default=CONFIG["limit_margin"])
    parser.add_argument("--persistence_window", type=int, default=CONFIG["persistence_window"])
    parser.add_argument("--persistence_min_points", type=int, default=CONFIG["persistence_min_points"])

    parser.add_argument("--level_z_limit", type=float, default=CONFIG["level_z_limit"])
    parser.add_argument("--spike_z_limit", type=float, default=CONFIG["spike_z_limit"])
    parser.add_argument("--change_z_limit", type=float, default=CONFIG["change_z_limit"])
    parser.add_argument("--volatility_z_limit", type=float, default=CONFIG["volatility_z_limit"])

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    CONFIG["data_file"] = args.data_file
    CONFIG["benchmark_result_file"] = args.benchmark_file
    CONFIG["output_file"] = args.output_file
    CONFIG["data_sheet_name"] = args.data_sheet_name
    CONFIG["benchmark_sheet_name"] = args.benchmark_sheet_name
    CONFIG["timestamp_col"] = args.timestamp_col

    CONFIG["clean_window_rows"] = args.clean_window_rows
    CONFIG["candidate_z_limit"] = args.candidate_z_limit
    CONFIG["threshold_k"] = args.threshold_k
    CONFIG["limit_margin"] = args.limit_margin
    CONFIG["persistence_window"] = args.persistence_window
    CONFIG["persistence_min_points"] = args.persistence_min_points

    CONFIG["level_z_limit"] = args.level_z_limit
    CONFIG["spike_z_limit"] = args.spike_z_limit
    CONFIG["change_z_limit"] = args.change_z_limit
    CONFIG["volatility_z_limit"] = args.volatility_z_limit

    main(CONFIG)
