"""
WITHOUT-CAUSAL TOP-5 CORRELATED FEATURE REGRESSION OUTLIER DETECTION (FAST)

No causal matrix is used.
For every target tag:
  1) Select top 5 best correlated tags from data.
  2) Build a regression model using only those top 5 tags.
  3) Predict the target value.
  4) Use actual-vs-predicted residual and historical value limits to classify:
       Normal, Drift, Contextual Anomaly, Drift + Anomaly, Strong Anomaly
  5) Detect within-limit outliers using residual_z even when actual value is inside limits.

Run:
  python without_causal_top5_corr_regression_fast.py \
    --data_file "Multi_X_Multi_Y_Correct_Data.xlsx" \
    --benchmark_file "context_aware_outlier_results(1).xlsx" \
    --output_file "without_causal_top5_corr_model_result.xlsx"

For All_Results style input:
  python without_causal_top5_corr_regression_fast.py \
    --data_file "context_aware_outlier_results(1).xlsx" \
    --data_sheet_name "All_Results" \
    --benchmark_file "context_aware_outlier_results(1).xlsx" \
    --benchmark_sheet_name "All_Results" \
    --output_file "without_causal_top5_corr_model_result.xlsx"

Requirements:
  pip install pandas numpy openpyxl xlsxwriter
"""

import os
import argparse
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


DEFAULTS = {
    "data_file": "Multi_X_Multi_Y_Correct_Data.xlsx",
    "data_sheet_name": None,
    "benchmark_file": "",
    "benchmark_sheet_name": "All_Results",
    "timestamp_col": "Timestamp",
    "tag_col": "Tag",
    "value_col": "Actual_Value",
    "output_file": "without_causal_top5_corr_model_result_csv.zip",
    "top_n_features": 5,
    "stable_z_limit": 3.5,
    "stable_bad_fraction_cutoff": 0.20,
    "min_train_rows": 80,
    # Aligned with Outlier detection V5 ladder: drift 4 / drift+anomaly 4.5 / strong 7
    "residual_z_limit": 4.0,
    "residual_strong_z_limit": 7.0,
    "value_z_limit": 4.0,
    "value_strong_z_limit": 4.5,
    "peer_shift_z_limit": 2.5,
    "peer_shift_fraction_limit": 0.40,
    "soft_low_pct": 5,
    "soft_high_pct": 95,
    "outer_low_pct": 1,
    "outer_high_pct": 99,
    "eps": 1e-9,
}


# ============================================================
# HELPERS
# ============================================================

def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    norm_map = {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}
    for cand in candidates:
        key = cand.lower().replace(" ", "_")
        if key in norm_map:
            return norm_map[key]
    return None


def safe_divide(a, b):
    if b is None or b == 0 or pd.isna(b):
        return np.nan
    return a / b


def robust_center_scale(s: pd.Series, eps: float = 1e-9) -> Tuple[float, float]:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) == 0:
        return np.nan, np.nan
    center = float(s.median())
    mad = float((s - center).abs().median())
    scale = 1.4826 * mad
    if pd.isna(scale) or scale < eps:
        scale = float(s.std())
    if pd.isna(scale) or scale < eps:
        scale = eps
    return center, scale


def robust_z(s: pd.Series, center=None, scale=None, eps: float = 1e-9) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    if center is None or scale is None:
        center, scale = robust_center_scale(s, eps)
    if pd.isna(scale) or scale < eps:
        scale = eps
    return (s - center) / scale


def binary_status(final_class):
    if pd.isna(final_class):
        return "Unknown"
    fc = str(final_class).strip().lower()
    if fc in ["normal", "ok", "good"]:
        return "Normal"
    if fc in ["", "unknown", "nan", "none"]:
        return "Unknown"
    return "Abnormal"


def normalize_timestamp(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def range_position(value, low5, high95, low1, high99):
    if pd.isna(value):
        return "Unknown"
    if value < low1:
        return "Below 1pct outer baseline"
    if value < low5:
        return "Below 5pct soft baseline"
    if value <= high95:
        return "Inside 5-95 baseline"
    if value <= high99:
        return "Above 95pct soft baseline"
    return "Above 99pct outer baseline"


# ============================================================
# LOAD DATA
# ============================================================

def read_excel(path: str, sheet_name=None) -> pd.DataFrame:
    if sheet_name is None or str(sheet_name).strip() == "":
        return pd.read_excel(path)
    return pd.read_excel(path, sheet_name=sheet_name)


def load_process_data(path: str, sheet_name, timestamp_col: str, tag_col: str, value_col: str):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    df = clean_column_names(read_excel(path, sheet_name))

    ts = find_column(df, [timestamp_col, "Timestamp", "Time", "DateTime", "Date"])
    if ts is None:
        raise ValueError("Timestamp column not found.")

    tag = find_column(df, [tag_col, "Tag", "Target_Tag", "Variable", "Column", "Parameter"])
    val = find_column(df, [value_col, "Actual_Value", "Actual Value", "Value"])

    # All_Results / long format
    if tag is not None and val is not None:
        long_df = df[[ts, tag, val]].copy()
        long_df.columns = ["Timestamp", "Tag", "Actual_Value"]
        long_df["Timestamp"] = normalize_timestamp(long_df["Timestamp"])
        long_df["Tag"] = long_df["Tag"].astype(str).str.strip()
        long_df["Actual_Value"] = pd.to_numeric(long_df["Actual_Value"], errors="coerce")
        long_df = long_df.dropna(subset=["Timestamp", "Tag"])
        wide = long_df.pivot_table(index="Timestamp", columns="Tag", values="Actual_Value", aggfunc="mean").reset_index()
        wide.columns = [str(c) for c in wide.columns]
        return wide, "Timestamp"

    # Wide format
    df[ts] = normalize_timestamp(df[ts])
    df = df.dropna(subset=[ts]).sort_values(ts).reset_index(drop=True)
    return df, ts


def get_numeric_tags(df, timestamp_col):
    tags = []
    for c in df.columns:
        if c == timestamp_col:
            continue
        x = pd.to_numeric(df[c], errors="coerce")
        if x.notna().sum() > 0:
            df[c] = x
            tags.append(c)
    return tags


# ============================================================
# FAST REGRESSION MODEL
# ============================================================

def detect_stable_rows(df: pd.DataFrame, tag_cols: List[str], config: Dict):
    z = pd.DataFrame(index=df.index)
    for c in tag_cols:
        z[c] = robust_z(df[c], eps=config["eps"])
    bad_fraction = (z.abs() > config["stable_z_limit"]).mean(axis=1)
    stable = bad_fraction <= config["stable_bad_fraction_cutoff"]
    return pd.DataFrame({
        "Row_Index": df.index,
        "Bad_Tag_Fraction": bad_fraction,
        "Is_Stable_Candidate": stable,
    })


def build_corr_matrix(df: pd.DataFrame, tag_cols: List[str], train_mask: pd.Series):
    # Pearson is fast and stable. Rank correlation can be enabled manually if needed.
    sub = df.loc[train_mask, tag_cols].apply(pd.to_numeric, errors="coerce")
    return sub.corr(method="pearson")


def standardize_train_all(X_train: pd.DataFrame, X_all: pd.DataFrame):
    med = X_train.median(numeric_only=True)
    X_train = X_train.fillna(med)
    X_all = X_all.fillna(med)
    mean = X_train.mean(axis=0)
    std = X_train.std(axis=0).replace(0, 1.0).fillna(1.0)
    return (X_train - mean) / std, (X_all - mean) / std


def fit_fast_ridge(X_train, y_train, X_all, alpha=1.0):
    y = pd.to_numeric(y_train, errors="coerce")
    valid = y.notna()
    X_train = X_train.loc[valid]
    y = y.loc[valid]
    if len(y) < 10:
        pred = np.repeat(float(y.median()) if len(y) else np.nan, len(X_all))
        return pred, pred[:len(y)], np.nan, {}

    Xs_train, Xs_all = standardize_train_all(X_train, X_all)
    X = np.column_stack([np.ones(len(Xs_train)), Xs_train.values])
    Xa = np.column_stack([np.ones(len(Xs_all)), Xs_all.values])

    # Ridge: beta = (X'X + alpha*I)^-1 X'y. Do not regularize intercept.
    I = np.eye(X.shape[1])
    I[0, 0] = 0
    try:
        beta = np.linalg.solve(X.T @ X + alpha * I, X.T @ y.values)
    except np.linalg.LinAlgError:
        beta = np.linalg.pinv(X.T @ X + alpha * I) @ X.T @ y.values

    pred_all = Xa @ beta
    pred_train = X @ beta
    ss_res = np.sum((y.values - pred_train) ** 2)
    ss_tot = np.sum((y.values - np.mean(y.values)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    importances = dict(zip(X_train.columns, np.abs(beta[1:])))
    return pred_all, pred_train, r2, importances


def classify(row, config):
    vz = abs(row["Value_Z"]) if pd.notna(row["Value_Z"]) else 0
    rz = abs(row["Residual_Z"]) if pd.notna(row["Residual_Z"]) else 0
    soft = bool(row["Soft_Range_Flag"])
    outer = bool(row["Outer_Range_Flag"])
    peer = row["Peer_Shift_Fraction"] if pd.notna(row["Peer_Shift_Fraction"]) else 0

    residual_anomaly = rz >= config["residual_z_limit"]
    residual_strong = rz >= config["residual_strong_z_limit"]
    value_strong = vz >= config["value_strong_z_limit"]
    peer_supported = peer >= config["peer_shift_fraction_limit"]

    if residual_strong or (outer and residual_anomaly) or (value_strong and residual_anomaly):
        return "Strong Anomaly"
    if soft and residual_anomaly:
        return "Drift + Anomaly"
    if residual_anomaly and not soft:
        return "Contextual Anomaly"
    if soft and (peer_supported or not residual_anomaly):
        return "Drift"
    return "Normal"


def severity(row):
    rz = abs(row["Residual_Z"]) if pd.notna(row["Residual_Z"]) else 0
    vz = abs(row["Value_Z"]) if pd.notna(row["Value_Z"]) else 0
    peer = row["Peer_Shift_Fraction"] if pd.notna(row["Peer_Shift_Fraction"]) else 0
    score = min(100, 12 * rz + 8 * vz + 20 * peer + (15 if row["Outer_Range_Flag"] else 0) + (8 if row["Soft_Range_Flag"] else 0))
    return round(float(score), 1)


def explanation(fc):
    if fc == "Normal":
        return "Actual is consistent with predicted value from top correlated tags."
    if fc == "Drift":
        return "Actual is outside historical value range, but related tags support the shift."
    if fc == "Contextual Anomaly":
        return "Within-limit outlier: value may be inside limits, but residual against predicted value is abnormal."
    if fc == "Drift + Anomaly":
        return "Value is outside historical range and also breaks relationship with top correlated tags."
    if fc == "Strong Anomaly":
        return "Extreme residual/range break against top correlated feature model."
    return "Detected by top correlated feature model."


def run_model(df, timestamp_col, tag_cols, config):
    stable_rows = detect_stable_rows(df, tag_cols, config)
    base_train_mask = stable_rows["Is_Stable_Candidate"].astype(bool)
    corr_matrix = build_corr_matrix(df, tag_cols, base_train_mask)

    all_results = []
    tag_summary = []

    for i, target in enumerate(tag_cols, 1):
        print(f"[{i}/{len(tag_cols)}] {target}", flush=True)
        y = pd.to_numeric(df[target], errors="coerce")
        train_mask = base_train_mask.copy() & y.notna()
        if train_mask.sum() < config["min_train_rows"]:
            yc, ys = robust_center_scale(y, config["eps"])
            train_mask = (robust_z(y, yc, ys, config["eps"]).abs() <= config["stable_z_limit"]) & y.notna()
        if train_mask.sum() < 20:
            train_mask = y.notna()

        corr_s = corr_matrix[target].drop(labels=[target], errors="ignore").dropna()
        top = corr_s.abs().sort_values(ascending=False).head(config["top_n_features"]).index.tolist()
        if len(top) == 0:
            continue

        X_train = df.loc[train_mask, top].apply(pd.to_numeric, errors="coerce")
        y_train = y.loc[train_mask]
        X_all = df[top].apply(pd.to_numeric, errors="coerce")
        pred, pred_train, r2, imps = fit_fast_ridge(X_train, y_train, X_all, alpha=1.0)

        resid = y.values - pred
        res_center, res_scale = robust_center_scale(pd.Series(resid, index=df.index).loc[train_mask], config["eps"])
        residual_z = (pd.Series(resid) - res_center) / res_scale

        ref = y.loc[train_mask].dropna()
        y_center, y_scale = robust_center_scale(ref, config["eps"])
        value_z = (y - y_center) / y_scale
        low5 = ref.quantile(config["soft_low_pct"] / 100.0)
        high95 = ref.quantile(config["soft_high_pct"] / 100.0)
        low1 = ref.quantile(config["outer_low_pct"] / 100.0)
        high99 = ref.quantile(config["outer_high_pct"] / 100.0)

        peer_shift_cols = []
        for f in top:
            fc, fs = robust_center_scale(df.loc[train_mask, f], config["eps"])
            fz = robust_z(df[f], fc, fs, config["eps"])
            peer_shift_cols.append((fz.abs() > config["peer_shift_z_limit"]).astype(float).values)
        peer_arr = np.vstack(peer_shift_cols).T if peer_shift_cols else np.zeros((len(df), 1))
        peer_fraction = np.nanmean(peer_arr, axis=1)
        group_support = np.nansum(peer_arr, axis=1)

        out = pd.DataFrame({
            "Timestamp": df[timestamp_col],
            "Tag": target,
            "Actual_Value": y,
            "Predicted_Value_From_Related_Tags": pred,
            "Residual": resid,
            "Historical_Low_5pct": low5,
            "Historical_High_95pct": high95,
            "Historical_Low_1pct": low1,
            "Historical_High_99pct": high99,
            "Value_Z": value_z,
            "Residual_Z": residual_z,
            "Group_Support": group_support,
            "Peer_Shift_Fraction": peer_fraction,
            "Primary_Drivers": ", ".join(top),
            "Prediction_Basis": "Top correlated features only - no causal matrix",
            "Model_Type": "Fast Ridge regression on top correlated tags",
            "Top_Correlated_Features": ", ".join([f"{f} ({corr_matrix.loc[target, f]:.3f})" for f in top]),
            "Model_R2_Train": r2,
        })
        out["Range_Position"] = [range_position(v, low5, high95, low1, high99) for v in out["Actual_Value"]]
        out["Soft_Range_Flag"] = (out["Actual_Value"] < low5) | (out["Actual_Value"] > high95)
        out["Outer_Range_Flag"] = (out["Actual_Value"] < low1) | (out["Actual_Value"] > high99)
        out["Final_Class"] = out.apply(lambda r: classify(r, config), axis=1)
        out["Final_Status"] = out["Final_Class"].apply(binary_status)
        out["Severity_Score_0_100"] = out.apply(severity, axis=1)
        out["Explanation"] = out["Final_Class"].apply(explanation)

        # Arrange similar to context-aware All_Results.
        out = out[[
            "Timestamp", "Tag", "Actual_Value", "Predicted_Value_From_Related_Tags", "Residual",
            "Historical_Low_5pct", "Historical_High_95pct", "Historical_Low_1pct", "Historical_High_99pct",
            "Range_Position", "Value_Z", "Residual_Z", "Group_Support", "Peer_Shift_Fraction",
            "Soft_Range_Flag", "Outer_Range_Flag", "Primary_Drivers", "Final_Class", "Final_Status",
            "Severity_Score_0_100", "Explanation", "Prediction_Basis", "Model_Type", "Top_Correlated_Features", "Model_R2_Train"
        ]]
        all_results.append(out)

        c = out["Final_Class"].value_counts().to_dict()
        tag_summary.append({
            "Tag": target,
            "Top_Related_Tags": ", ".join(top),
            "Top_Correlations": ", ".join([f"{f}:{corr_matrix.loc[target, f]:.3f}" for f in top]),
            "Model_R2_Train": r2,
            "Train_Rows": int(train_mask.sum()),
            "Normal_Count": c.get("Normal", 0),
            "Drift_Count": c.get("Drift", 0),
            "Contextual_Anomaly_Count": c.get("Contextual Anomaly", 0),
            "Drift_Anomaly_Count": c.get("Drift + Anomaly", 0),
            "Strong_Anomaly_Count": c.get("Strong Anomaly", 0),
            "Non_Normal_Count": int((out["Final_Status"] == "Abnormal").sum()),
            "Avg_Severity": out["Severity_Score_0_100"].mean(),
            "Max_Severity": out["Severity_Score_0_100"].max(),
        })

    all_df = pd.concat(all_results, ignore_index=True)
    tag_df = pd.DataFrame(tag_summary).sort_values("Non_Normal_Count", ascending=False)
    class_order = ["Normal", "Drift", "Contextual Anomaly", "Drift + Anomaly", "Strong Anomaly"]
    event = all_df.groupby(["Timestamp", "Final_Class"]).size().unstack(fill_value=0).reindex(columns=class_order, fill_value=0).reset_index()
    event["Total_Abnormal_Count"] = event[[c for c in class_order if c != "Normal"]].sum(axis=1)
    top_tags = (all_df[all_df["Final_Status"] == "Abnormal"]
                .sort_values(["Timestamp", "Severity_Score_0_100"], ascending=[True, False])
                .groupby("Timestamp")["Tag"].apply(lambda x: ", ".join(x.head(8))))
    event["Top_Non_Normal_Tags"] = event["Timestamp"].map(top_tags).fillna("")
    anomalies = all_df[all_df["Final_Status"] == "Abnormal"].sort_values(["Timestamp", "Severity_Score_0_100"], ascending=[True, False])
    return all_df, tag_df, event, anomalies, stable_rows


# ============================================================
# BENCHMARK COMPARISON
# ============================================================

def load_benchmark(path, sheet):
    if path is None or str(path).strip() == "" or not os.path.exists(path):
        return None
    xl = pd.ExcelFile(path)
    use_sheet = sheet if sheet in xl.sheet_names else None
    if use_sheet is None:
        for s in xl.sheet_names:
            if "all" in s.lower() and "result" in s.lower():
                use_sheet = s
                break
    if use_sheet is None:
        return None
    return clean_column_names(pd.read_excel(path, sheet_name=use_sheet))


def standardize_benchmark(b):
    ts = find_column(b, ["Timestamp", "Time", "DateTime", "Date"])
    tag = find_column(b, ["Tag", "Target_Tag", "Variable", "Column", "Parameter"])
    fc = find_column(b, ["Final_Class", "Final Class", "Class", "Status"])
    fs = find_column(b, ["Final_Status", "Final Status", "Binary_Status"])
    if ts is None or tag is None or fc is None:
        raise ValueError("Benchmark must contain Timestamp, Tag, Final_Class/Status.")
    out = b[[ts, tag, fc]].copy()
    out.columns = ["Timestamp", "Tag", "Benchmark_Final_Class"]
    out["Benchmark_Final_Status"] = b[fs].apply(binary_status) if fs is not None else out["Benchmark_Final_Class"].apply(binary_status)
    out["Timestamp"] = normalize_timestamp(out["Timestamp"])
    out["Tag"] = out["Tag"].astype(str).str.strip()
    return out.dropna(subset=["Timestamp", "Tag"])


def compare(all_results, benchmark):
    if benchmark is None:
        return None
    b = standardize_benchmark(benchmark)
    a = all_results[["Timestamp", "Tag", "Actual_Value", "Predicted_Value_From_Related_Tags", "Residual", "Value_Z", "Residual_Z", "Primary_Drivers", "Final_Class", "Final_Status"]].copy()
    a = a.rename(columns={"Final_Class": "Model_Final_Class", "Final_Status": "Model_Final_Status"})
    a["Timestamp"] = normalize_timestamp(a["Timestamp"])
    a["Tag"] = a["Tag"].astype(str).str.strip()
    comp = a.merge(b, on=["Timestamp", "Tag"], how="inner")
    comp["Class_Match"] = comp["Model_Final_Class"].str.lower() == comp["Benchmark_Final_Class"].astype(str).str.lower()
    comp["Binary_Match"] = comp["Model_Final_Status"].str.lower() == comp["Benchmark_Final_Status"].str.lower()
    comp["Comparison_Result"] = np.select([
        (comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Model_Final_Status"] == "Abnormal"),
        (comp["Benchmark_Final_Status"] == "Normal") & (comp["Model_Final_Status"] == "Normal"),
        (comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Model_Final_Status"] == "Normal"),
        (comp["Benchmark_Final_Status"] == "Normal") & (comp["Model_Final_Status"] == "Abnormal"),
    ], ["Both Abnormal", "Both Normal", "Benchmark Only Abnormal", "Model Only Abnormal"], default="Other")
    tp = ((comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Model_Final_Status"] == "Abnormal")).sum()
    tn = ((comp["Benchmark_Final_Status"] == "Normal") & (comp["Model_Final_Status"] == "Normal")).sum()
    fp = ((comp["Benchmark_Final_Status"] == "Normal") & (comp["Model_Final_Status"] == "Abnormal")).sum()
    fn = ((comp["Benchmark_Final_Status"] == "Abnormal") & (comp["Model_Final_Status"] == "Normal")).sum()
    total = len(comp)
    binary_summary = pd.DataFrame([{
        "Total_Matched_Rows": total,
        "TP_Both_Abnormal": int(tp),
        "TN_Both_Normal": int(tn),
        "FP_Model_Only": int(fp),
        "FN_Benchmark_Only": int(fn),
        "Benchmark_Abnormal_Rows": int((comp["Benchmark_Final_Status"] == "Abnormal").sum()),
        "Model_Abnormal_Rows": int((comp["Model_Final_Status"] == "Abnormal").sum()),
        "Binary_Agreement_Accuracy": safe_divide(tp + tn, total),
        "Precision_vs_Benchmark": safe_divide(tp, tp + fp),
        "Recall_vs_Benchmark": safe_divide(tp, tp + fn),
        "Specificity_vs_Benchmark": safe_divide(tn, tn + fp),
        "Exact_Final_Class_Match": comp["Class_Match"].mean() if total else np.nan,
    }])
    class_comp = pd.crosstab(comp["Benchmark_Final_Class"], comp["Model_Final_Class"], margins=True).reset_index()
    bin_comp = pd.crosstab(comp["Benchmark_Final_Status"], comp["Model_Final_Status"], margins=True).reset_index()
    by_tag = []
    for tag, g in comp.groupby("Tag"):
        tp_t = ((g["Benchmark_Final_Status"] == "Abnormal") & (g["Model_Final_Status"] == "Abnormal")).sum()
        tn_t = ((g["Benchmark_Final_Status"] == "Normal") & (g["Model_Final_Status"] == "Normal")).sum()
        fp_t = ((g["Benchmark_Final_Status"] == "Normal") & (g["Model_Final_Status"] == "Abnormal")).sum()
        fn_t = ((g["Benchmark_Final_Status"] == "Abnormal") & (g["Model_Final_Status"] == "Normal")).sum()
        by_tag.append({
            "Tag": tag, "Total_Rows": len(g), "TP_Both_Abnormal": int(tp_t), "TN_Both_Normal": int(tn_t),
            "FP_Model_Only": int(fp_t), "FN_Benchmark_Only": int(fn_t),
            "Benchmark_Abnormal": int((g["Benchmark_Final_Status"] == "Abnormal").sum()),
            "Model_Abnormal": int((g["Model_Final_Status"] == "Abnormal").sum()),
            "Accuracy": safe_divide(tp_t + tn_t, len(g)), "Precision": safe_divide(tp_t, tp_t + fp_t),
            "Recall": safe_divide(tp_t, tp_t + fn_t), "Specificity": safe_divide(tn_t, tn_t + fp_t),
            "Exact_Class_Match": g["Class_Match"].mean(),
        })
    by_tag = pd.DataFrame(by_tag).sort_values(["Recall", "Precision"], ascending=[True, False])
    by_time = comp.groupby("Timestamp").agg(
        Total_Tags=("Tag", "count"),
        Benchmark_Abnormal_Count=("Benchmark_Final_Status", lambda x: (x == "Abnormal").sum()),
        Model_Abnormal_Count=("Model_Final_Status", lambda x: (x == "Abnormal").sum()),
        Binary_Match_Count=("Binary_Match", "sum"),
        Class_Match_Count=("Class_Match", "sum"),
    ).reset_index()
    by_time["Binary_Match_Rate"] = by_time["Binary_Match_Count"] / by_time["Total_Tags"]
    by_time["Class_Match_Rate"] = by_time["Class_Match_Count"] / by_time["Total_Tags"]
    disagree = comp[comp["Binary_Match"] == False].copy()
    return {"Binary_Summary": binary_summary, "Class_Comparison": class_comp, "Binary_Comparison": bin_comp, "Comparison_By_Tag": by_tag, "Comparison_By_Timestamp": by_time, "Comparison_Row_Tag": comp, "Disagreements": disagree}


# ============================================================
# EXPORT
# ============================================================

def make_summary(all_results, tag_summary, stable_rows, config, comp=None):
    total = len(all_results)
    abnormal = int((all_results["Final_Status"] == "Abnormal").sum())
    normal = int((all_results["Final_Status"] == "Normal").sum())
    rows = [
        {"Metric": "Method", "Value": "Without causal - Top 5 correlated feature regression model"},
        {"Metric": "Causal Matrix Used", "Value": "No"},
        {"Metric": "Feature Selection", "Value": f"Top {config['top_n_features']} absolute correlation features per tag"},
        {"Metric": "Model", "Value": "Fast Ridge regression"},
        {"Metric": "Residual_Z limit (|residual z| abnormal)", "Value": config["residual_z_limit"]},
        {"Metric": "Value_Z strong tier (|value z|)", "Value": config["value_strong_z_limit"]},
        {"Metric": "Residual_Z strong limit", "Value": config["residual_strong_z_limit"]},
        {"Metric": "Within-Limit Outlier Detection", "Value": "Actual may be inside limits, but residual_z can still trigger Contextual Anomaly"},
        {"Metric": "Total Tag-Timestamp Rows", "Value": total},
        {"Metric": "Normal Rows", "Value": normal},
        {"Metric": "Abnormal Rows", "Value": abnormal},
        {"Metric": "Abnormal Rate", "Value": safe_divide(abnormal, total)},
        {"Metric": "Tags Modeled", "Value": tag_summary["Tag"].nunique()},
        {"Metric": "Stable Candidate Rows", "Value": int(stable_rows["Is_Stable_Candidate"].sum())},
    ]
    if comp is not None:
        for k, v in comp["Binary_Summary"].iloc[0].to_dict().items():
            rows.append({"Metric": f"Comparison - {k}", "Value": v})
    return pd.DataFrame(rows)


def write_excel(output_file, sheets):
    """
    Fast export.
    - If output_file ends with .zip: writes one CSV per sheet into a ZIP. Recommended for large results.
    - If output_file ends with .xlsx: writes Excel using xlsxwriter. This can be slower for very large All_Results sheets.
    """
    import zipfile
    from pathlib import Path

    output_file = str(output_file)

    if output_file.lower().endswith(".zip"):
        temp_dir = Path(output_file).with_suffix("")
        temp_dir.mkdir(parents=True, exist_ok=True)
        csv_paths = []
        for name, df in sheets.items():
            if df is None:
                continue
            csv_path = temp_dir / f"{name[:31]}.csv"
            df.to_csv(csv_path, index=False)
            csv_paths.append(csv_path)

        with zipfile.ZipFile(output_file, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for csv_path in csv_paths:
                z.write(csv_path, arcname=csv_path.name)
        print(f"Generated ZIP CSV result: {output_file}")
        return

    with pd.ExcelWriter(output_file, engine="xlsxwriter") as writer:
        for name, df in sheets.items():
            if df is None:
                continue
            df.to_excel(writer, sheet_name=name[:31], index=False)
    print(f"Generated Excel: {output_file}")

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--data_file", default=DEFAULTS["data_file"])
    p.add_argument("--data_sheet_name", default=DEFAULTS["data_sheet_name"])
    p.add_argument("--benchmark_file", default=DEFAULTS["benchmark_file"])
    p.add_argument("--benchmark_sheet_name", default=DEFAULTS["benchmark_sheet_name"])
    p.add_argument("--timestamp_col", default=DEFAULTS["timestamp_col"])
    p.add_argument("--tag_col", default=DEFAULTS["tag_col"])
    p.add_argument("--value_col", default=DEFAULTS["value_col"])
    p.add_argument("--output_file", default=DEFAULTS["output_file"])
    p.add_argument("--top_n_features", type=int, default=DEFAULTS["top_n_features"])
    p.add_argument("--residual_z_limit", type=float, default=DEFAULTS["residual_z_limit"])
    p.add_argument("--residual_strong_z_limit", type=float, default=DEFAULTS["residual_strong_z_limit"])
    p.add_argument("--peer_shift_fraction_limit", type=float, default=DEFAULTS["peer_shift_fraction_limit"])
    return p.parse_args()


def main():
    args = parse_args()
    config = DEFAULTS.copy()
    config.update(vars(args))

    print("Loading data...", flush=True)
    df, ts = load_process_data(config["data_file"], config["data_sheet_name"], config["timestamp_col"], config["tag_col"], config["value_col"])
    df = clean_column_names(df)
    df[ts] = normalize_timestamp(df[ts])
    df = df.dropna(subset=[ts]).sort_values(ts).reset_index(drop=True)
    tag_cols = get_numeric_tags(df, ts)
    if len(tag_cols) < 2:
        raise ValueError("At least two numeric tags are required.")
    print(f"Rows={len(df)}, Tags={len(tag_cols)}", flush=True)

    all_results, tag_summary, event_summary, anomalies, stable_rows = run_model(df, ts, tag_cols, config)

    bench = load_benchmark(config["benchmark_file"], config["benchmark_sheet_name"])
    comp = compare(all_results, bench) if bench is not None else None
    summary = make_summary(all_results, tag_summary, stable_rows, config, comp)

    status_mapping = pd.DataFrame([
        {"Condition": "Outside 5-95 range; residual normal/peer supported", "Final_Class": "Drift"},
        {"Condition": "Inside limits but residual_z >= threshold", "Final_Class": "Contextual Anomaly"},
        {"Condition": "Outside range and residual_z abnormal", "Final_Class": "Drift + Anomaly"},
        {"Condition": "Outer range + residual break or very strong residual", "Final_Class": "Strong Anomaly"},
        {"Condition": "No range or residual abnormality", "Final_Class": "Normal"},
    ])
    sheets = {
        "Summary": summary,
        "Status_Mapping": status_mapping,
        "Tag_Summary": tag_summary,
        "Event_Summary": event_summary,
        "Anomalies_Only": anomalies,
        "All_Results": all_results,
        "Stable_Rows": stable_rows,
    }
    if comp is not None:
        sheets.update(comp)
    write_excel(config["output_file"], sheets)


if __name__ == "__main__":
    main()
