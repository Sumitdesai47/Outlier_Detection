import pandas as pd
import numpy as np
import warnings
import re
from scipy.stats import ks_2samp, spearmanr
from statsmodels.tsa.stattools import grangercausalitytests
from sklearn.metrics import r2_score, mean_squared_error
from xgboost import XGBRegressor
import shap

warnings.filterwarnings("ignore")


# =========================================================
# CONFIGURATION
# =========================================================
CONFIG = {
    # -------- file inputs --------
    "data_file": "Multi_X_Multi_Y_Correct_Data.csv",
    "cause_file": "causes_for_target (2).xlsx",   # optional if cause list/path file exists
    "output_file": "Generic_Root_Cause_Report.xlsx",

    # -------- data settings --------
    "timestamp_col": "Timestamp",
    "target_col": "C2_Splitter_DP",               # change target here
    "start_date": "2024-02-01",
    "end_date": "2024-05-31 23:59:59",

    # -------- optional excel sheets --------
    "use_all_causes_sheet": True,
    "all_causes_sheet_name": "All_Causes",

    "use_example_paths_sheet": True,
    "example_paths_sheet_name": "Example_Paths",

    # -------- fallback mode --------
    # if All_Causes sheet is not available, use all numeric columns except timestamp and target
    "fallback_use_all_numeric_as_causes": True,

    # -------- smoothing --------
    "use_time_based_smoothing": True,
    "rolling_window": "5D",        # true 5-day smoothing
    "rolling_min_periods": 1,

    # -------- split --------
    "historic_ratio": 0.70,

    # -------- model / analysis --------
    "max_analysis_lag": 7,
    "max_model_lag": 3,
    "min_required_rows": 30,

    # -------- top outputs --------
    "top_n_direct": 15,
    "top_n_indirect": 15,
    "top_n_root_causes": 5,

    # -------- scoring weights --------
    "weights": {
        "shap": 0.30,
        "granger": 0.20,
        "lag": 0.15,
        "drift_lead": 0.15,
        "path_bonus": 0.10,
        "corr": 0.10
    }
}


# =========================================================
# HELPERS
# =========================================================
def safe_numeric(series):
    return pd.to_numeric(series, errors="coerce")


def normalize_series(s):
    s = pd.Series(s).astype(float)
    if len(s) == 0 or s.nunique(dropna=True) <= 1:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - s.min()) / (s.max() - s.min() + 1e-9)


def parse_path_tags(path_text):
    if pd.isna(path_text):
        return []
    txt = str(path_text).strip()
    parts = re.split(r"->|→", txt)
    return [p.strip() for p in parts if str(p).strip() != ""]


def compute_drift_metrics(ref, cur):
    ref = ref.dropna()
    cur = cur.dropna()

    if len(ref) < 5 or len(cur) < 5:
        return {
            "ks_stat": np.nan,
            "p_value": np.nan,
            "mean_shift": np.nan,
            "std_shift": np.nan,
            "drift_flag": False
        }

    ks_stat, p_value = ks_2samp(ref, cur)

    ref_mean = ref.mean()
    ref_std = ref.std()
    cur_mean = cur.mean()
    cur_std = cur.std()

    mean_shift = abs(cur_mean - ref_mean) / (abs(ref_mean) + 1e-6)
    std_shift = abs(cur_std - ref_std) / (abs(ref_std) + 1e-6)

    drift_flag = (p_value < 0.05) or (mean_shift > 0.20) or (std_shift > 0.20)

    return {
        "ks_stat": ks_stat,
        "p_value": p_value,
        "mean_shift": mean_shift,
        "std_shift": std_shift,
        "drift_flag": drift_flag
    }


def detect_first_drift_time(df, col, split_index, timestamp_col):
    hist = df.iloc[:split_index]
    cur = df.iloc[split_index:]

    ref = hist[col].dropna()
    if len(ref) < 5:
        return None

    ref_mean = ref.mean()
    ref_std = ref.std()

    upper = ref_mean + 3 * ref_std
    lower = ref_mean - 3 * ref_std

    drift_rows = cur[(cur[col] > upper) | (cur[col] < lower)]
    if len(drift_rows) == 0:
        return None

    return drift_rows.iloc[0][timestamp_col]


def get_best_leading_lag(x, y, max_lag=7):
    temp = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(temp) < max_lag + 10:
        return np.nan, np.nan

    x = temp["x"]
    y = temp["y"]

    best_lag = np.nan
    best_corr = np.nan
    best_abs_corr = -999

    for lag in range(1, max_lag + 1):
        corr = x.shift(lag).corr(y)
        if pd.notna(corr) and abs(corr) > best_abs_corr:
            best_abs_corr = abs(corr)
            best_lag = lag
            best_corr = corr

    return best_lag, best_corr


def granger_score(x, y, max_lag=7):
    temp = pd.DataFrame({"y": y, "x": x}).dropna()

    if len(temp) < max_lag + 15:
        return np.nan, np.nan, 0.0

    if temp["x"].nunique() < 3 or temp["y"].nunique() < 3:
        return np.nan, np.nan, 0.0

    try:
        res = grangercausalitytests(temp[["y", "x"]], maxlag=max_lag, verbose=False)
        pvals = {lag: res[lag][0]["ssr_ftest"][1] for lag in range(1, max_lag + 1)}

        best_lag = min(pvals, key=pvals.get)
        best_p = pvals[best_lag]
        score = max(0.0, min(1.0, -np.log10(best_p + 1e-12) / 10))
        return best_lag, best_p, score

    except Exception:
        return np.nan, np.nan, 0.0


def drift_lead_score(x_drift_time, y_drift_time, max_gap_days=20):
    if x_drift_time is None or y_drift_time is None:
        return 0.0

    x_time = pd.to_datetime(x_drift_time)
    y_time = pd.to_datetime(y_drift_time)

    if x_time > y_time:
        return 0.0

    gap_days = (y_time - x_time).days
    if gap_days < 0 or gap_days > max_gap_days:
        return 0.0

    return max(0.0, 1 - gap_days / max_gap_days)


def create_lagged_features(df, feature_cols, target_col, max_lag=3):
    out = pd.DataFrame(index=df.index)
    out[target_col] = df[target_col]

    for col in feature_cols:
        out[col] = df[col]
        for lag in range(1, max_lag + 1):
            out[f"{col}_lag{lag}"] = df[col].shift(lag)

    return out


def path_bonus_info(paths_list, target_col, x_col):
    direct_bonus = 0
    indirect_bonus = 0
    matched_paths = []

    for path in paths_list:
        tags = parse_path_tags(path)
        if target_col not in tags:
            continue

        target_idx = tags.index(target_col)
        prefix = tags[:target_idx]

        if x_col in prefix:
            matched_paths.append(path)
            if len(prefix) > 0 and prefix[-1] == x_col:
                direct_bonus = 1
            else:
                indirect_bonus = 1

    return direct_bonus, indirect_bonus, matched_paths


def infer_candidate_causes(df, timestamp_col, target_col):
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    return [c for c in numeric_cols if c not in [timestamp_col, target_col]]


def build_chain_from_data(all_scores_df, target_col, top_root_causes, max_links=3):
    """
    Generic fallback chain builder from scores.
    If explicit example path is not available, build simple chain:
    X -> target
    or X -> intermediate -> target where possible.
    """
    rows = []

    ranked = all_scores_df.sort_values("Root_Cause_Score", ascending=False).copy()

    for root_tag in top_root_causes:
        root_info = ranked[ranked["X_Tag"] == root_tag]
        if len(root_info) == 0:
            continue

        # find likely intermediate variables influenced by root_tag
        candidate_mid = ranked[
            (ranked["X_Tag"] != root_tag) &
            (ranked["X_Tag"] != target_col)
        ].head(20)["X_Tag"].tolist()

        # simple fallback
        rows.append({
            "Top_Root_Cause": root_tag,
            "Propagation_Path": f"{root_tag} -> {target_col}",
            "Path_Source": "Data_Inferred_Fallback"
        })

    return pd.DataFrame(rows)


# =========================================================
# MAIN PIPELINE
# =========================================================
def generic_root_cause_pipeline(config):
    timestamp_col = config["timestamp_col"]
    target_col = config["target_col"]

    # -------------------------
    # load main data
    # -------------------------
    df = pd.read_csv(config["data_file"])
    df[timestamp_col] = pd.to_datetime(df[timestamp_col], errors="coerce")
    df = df.sort_values(timestamp_col).reset_index(drop=True)

    df = df[
        (df[timestamp_col] >= config["start_date"]) &
        (df[timestamp_col] <= config["end_date"])
    ].copy().reset_index(drop=True)

    if target_col not in df.columns:
        raise ValueError(f"Target column '{target_col}' not found in data file.")

    # -------------------------
    # load optional cause/path file
    # -------------------------
    candidate_causes = []
    example_paths = []

    cause_file_loaded = False
    if config.get("cause_file"):
        try:
            cause_xl = pd.ExcelFile(config["cause_file"])
            cause_file_loaded = True

            if config["use_all_causes_sheet"]:
                all_causes_df = pd.read_excel(
                    cause_xl,
                    sheet_name=config["all_causes_sheet_name"]
                )
                candidate_causes = (
                    all_causes_df.iloc[:, 0]
                    .dropna()
                    .astype(str)
                    .str.strip()
                    .unique()
                    .tolist()
                )

            if config["use_example_paths_sheet"]:
                example_paths_df = pd.read_excel(
                    cause_xl,
                    sheet_name=config["example_paths_sheet_name"]
                )
                example_paths = (
                    example_paths_df.iloc[:, 0]
                    .dropna()
                    .astype(str)
                    .tolist()
                )

        except Exception as e:
            print(f"Cause file load warning: {e}")
            cause_file_loaded = False

    # -------------------------
    # numeric conversion
    # -------------------------
    for col in df.columns:
        if col != timestamp_col:
            df[col] = safe_numeric(df[col])

    # -------------------------
    # fallback candidate cause selection
    # -------------------------
    if len(candidate_causes) == 0 and config["fallback_use_all_numeric_as_causes"]:
        candidate_causes = infer_candidate_causes(df, timestamp_col, target_col)

    candidate_causes = [c for c in candidate_causes if c in df.columns and c != target_col]

    if len(candidate_causes) == 0:
        raise ValueError("No valid candidate causes found.")

    # -------------------------
    # keep required columns only
    # -------------------------
    work_df = df[[timestamp_col, target_col] + candidate_causes].copy()
    work_df = work_df.dropna(subset=[target_col]).reset_index(drop=True)

    # -------------------------
    # smoothing
    # -------------------------
    if config["use_time_based_smoothing"]:
        tmp = work_df.set_index(timestamp_col).copy()
        for col in [target_col] + candidate_causes:
            tmp[col] = tmp[col].rolling(
                config["rolling_window"],
                min_periods=config["rolling_min_periods"]
            ).mean()
        work_df = tmp.reset_index()

    # -------------------------
    # split for drift
    # -------------------------
    split_index = int(len(work_df) * config["historic_ratio"])
    historic = work_df.iloc[:split_index].copy()
    current = work_df.iloc[split_index:].copy()

    target_drift = compute_drift_metrics(historic[target_col], current[target_col])
    target_drift_time = detect_first_drift_time(work_df, target_col, split_index, timestamp_col)

    # -------------------------
    # model data
    # -------------------------
    model_df = create_lagged_features(
        work_df,
        candidate_causes,
        target_col,
        max_lag=config["max_model_lag"]
    )
    model_df[timestamp_col] = work_df[timestamp_col].values
    model_df = model_df.dropna().reset_index(drop=True)

    feature_cols = [c for c in model_df.columns if c not in [timestamp_col, target_col]]
    X = model_df[feature_cols].copy()
    y = model_df[target_col].copy()

    split_idx_model = int(len(model_df) * 0.80)
    X_train = X.iloc[:split_idx_model]
    X_test = X.iloc[split_idx_model:]
    y_train = y.iloc[:split_idx_model]
    y_test = y.iloc[split_idx_model:]

    model = XGBRegressor(
        n_estimators=400,
        max_depth=4,
        learning_rate=0.03,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_alpha=0.5,
        reg_lambda=1.0,
        random_state=42
    )
    model.fit(X_train, y_train)

    y_pred_train = model.predict(X_train)
    y_pred_test = model.predict(X_test)

    train_r2 = r2_score(y_train, y_pred_train)
    test_r2 = r2_score(y_test, y_pred_test)
    test_rmse = np.sqrt(mean_squared_error(y_test, y_pred_test))

    # -------------------------
    # SHAP
    # -------------------------
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)

    shap_df = pd.DataFrame({
        "Feature": feature_cols,
        "Mean_Abs_SHAP": np.abs(shap_values).mean(axis=0)
    })

    base_tag_shap = []
    for tag in candidate_causes:
        tag_features = [f for f in shap_df["Feature"] if f == tag or f.startswith(f"{tag}_lag")]
        score = shap_df[shap_df["Feature"].isin(tag_features)]["Mean_Abs_SHAP"].sum()
        base_tag_shap.append({"X_Tag": tag, "Base_SHAP": score})

    base_shap_df = pd.DataFrame(base_tag_shap)

    # -------------------------
    # per-tag analysis
    # -------------------------
    all_rows = []

    for x_col in candidate_causes:
        temp = work_df[[timestamp_col, x_col, target_col]].copy().dropna().reset_index(drop=True)

        if len(temp) < config["min_required_rows"]:
            continue

        x_split = int(len(temp) * config["historic_ratio"])
        x_hist = temp.iloc[:x_split][x_col]
        x_cur = temp.iloc[x_split:][x_col]

        x_drift = compute_drift_metrics(x_hist, x_cur)
        x_drift_time = detect_first_drift_time(temp, x_col, x_split, timestamp_col)

        best_lag, lag_corr = get_best_leading_lag(
            temp[x_col], temp[target_col], max_lag=config["max_analysis_lag"]
        )

        g_lag, g_p, g_score = granger_score(
            temp[x_col], temp[target_col], max_lag=config["max_analysis_lag"]
        )

        sp_corr, _ = spearmanr(temp[x_col], temp[target_col], nan_policy="omit")
        if pd.isna(sp_corr):
            sp_corr = 0.0

        direct_bonus, indirect_bonus, matched_paths = path_bonus_info(example_paths, target_col, x_col)
        dlead = drift_lead_score(x_drift_time, target_drift_time, max_gap_days=20)

        all_rows.append({
            "X_Tag": x_col,
            "Y_Tag": target_col,

            "X_Drift_Flag": x_drift["drift_flag"],
            "X_Drift_Time": x_drift_time,
            "Y_Drift_Time": target_drift_time,

            "KS_Stat": x_drift["ks_stat"],
            "p_value": x_drift["p_value"],
            "Mean_Shift": x_drift["mean_shift"],
            "Std_Shift": x_drift["std_shift"],

            "Best_Lead_Lag": best_lag,
            "Lag_Correlation": lag_corr,

            "Granger_Best_Lag": g_lag,
            "Granger_p_value": g_p,
            "Granger_Score": g_score,

            "Spearman_Corr": sp_corr,
            "Direct_Path_Bonus": direct_bonus,
            "Indirect_Path_Bonus": indirect_bonus,
            "Matched_Paths": " | ".join(matched_paths) if len(matched_paths) > 0 else "",

            "Drift_Lead_Score": dlead
        })

    all_scores_df = pd.DataFrame(all_rows)
    all_scores_df = all_scores_df.merge(base_shap_df, on="X_Tag", how="left")
    all_scores_df["Base_SHAP"] = all_scores_df["Base_SHAP"].fillna(0.0)

    # -------------------------
    # scoring
    # -------------------------
    w = config["weights"]

    all_scores_df["SHAP_Score"] = normalize_series(all_scores_df["Base_SHAP"])
    all_scores_df["Lag_Score"] = normalize_series(all_scores_df["Lag_Correlation"].abs())
    all_scores_df["Corr_Score"] = normalize_series(all_scores_df["Spearman_Corr"].abs())
    all_scores_df["Direct_Path_Score"] = all_scores_df["Direct_Path_Bonus"].astype(float)
    all_scores_df["Indirect_Path_Score"] = all_scores_df["Indirect_Path_Bonus"].astype(float)

    all_scores_df["Direct_Cause_Score"] = (
        w["shap"]       * all_scores_df["SHAP_Score"].fillna(0) +
        w["granger"]    * all_scores_df["Granger_Score"].fillna(0) +
        w["lag"]        * all_scores_df["Lag_Score"].fillna(0) +
        w["drift_lead"] * all_scores_df["Drift_Lead_Score"].fillna(0) +
        w["path_bonus"] * all_scores_df["Direct_Path_Score"].fillna(0) +
        w["corr"]       * all_scores_df["Corr_Score"].fillna(0)
    )

    all_scores_df["Indirect_Cause_Score"] = (
        0.20 * all_scores_df["SHAP_Score"].fillna(0) +
        0.20 * all_scores_df["Granger_Score"].fillna(0) +
        0.15 * all_scores_df["Lag_Score"].fillna(0) +
        0.15 * all_scores_df["Drift_Lead_Score"].fillna(0) +
        0.25 * all_scores_df["Indirect_Path_Score"].fillna(0) +
        0.05 * all_scores_df["Corr_Score"].fillna(0)
    )

    all_scores_df["Root_Cause_Score"] = np.maximum(
        all_scores_df["Direct_Cause_Score"],
        0.9 * all_scores_df["Indirect_Cause_Score"]
    )

    all_scores_df["Cause_Type"] = np.where(
        all_scores_df["Direct_Cause_Score"] >= all_scores_df["Indirect_Cause_Score"],
        "Direct",
        "Indirect"
    )

    # -------------------------
    # outputs
    # -------------------------
    direct_causes_df = all_scores_df.sort_values("Direct_Cause_Score", ascending=False).reset_index(drop=True)
    indirect_causes_df = all_scores_df.sort_values("Indirect_Cause_Score", ascending=False).reset_index(drop=True)
    top_root_df = all_scores_df.sort_values("Root_Cause_Score", ascending=False).head(config["top_n_root_causes"]).copy()

    # propagation paths
    path_rows = []
    if len(example_paths) > 0:
        for _, row in top_root_df.iterrows():
            x_tag = row["X_Tag"]
            found = False

            for path in example_paths:
                tags = parse_path_tags(path)
                if target_col in tags and x_tag in tags:
                    x_idx = tags.index(x_tag)
                    y_idx = tags.index(target_col)
                    if x_idx < y_idx:
                        found = True
                        path_rows.append({
                            "Top_Root_Cause": x_tag,
                            "Cause_Type": row["Cause_Type"],
                            "Root_Cause_Score": row["Root_Cause_Score"],
                            "Propagation_Path": " -> ".join(tags[x_idx:y_idx+1]),
                            "Path_Source": "Example_Paths"
                        })

            if not found:
                path_rows.append({
                    "Top_Root_Cause": x_tag,
                    "Cause_Type": row["Cause_Type"],
                    "Root_Cause_Score": row["Root_Cause_Score"],
                    "Propagation_Path": f"{x_tag} -> {target_col}",
                    "Path_Source": "Fallback"
                })

        propagation_df = pd.DataFrame(path_rows)
    else:
        propagation_df = build_chain_from_data(
            all_scores_df=all_scores_df,
            target_col=target_col,
            top_root_causes=top_root_df["X_Tag"].tolist()
        )

    summary_df = pd.DataFrame([{
        "Target": target_col,
        "Date_Range_Start": config["start_date"],
        "Date_Range_End": config["end_date"],
        "Smoothing": config["rolling_window"] if config["use_time_based_smoothing"] else "None",
        "Historic_Ratio": config["historic_ratio"],
        "Target_Drift_Flag": target_drift["drift_flag"],
        "Target_Drift_Time": target_drift_time,
        "Model_Train_R2": train_r2,
        "Model_Test_R2": test_r2,
        "Model_Test_RMSE": test_rmse,
        "Total_Candidate_Causes": len(candidate_causes),
        "Cause_File_Loaded": cause_file_loaded,
        "Used_Example_Paths": len(example_paths) > 0
    }])

    # -------------------------
    # save excel
    # -------------------------
    with pd.ExcelWriter(config["output_file"], engine="openpyxl") as writer:
        work_df.to_excel(writer, sheet_name="Smoothed_Data", index=False)
        shap_df.to_excel(writer, sheet_name="Raw_SHAP_Features", index=False)
        base_shap_df.sort_values("Base_SHAP", ascending=False).to_excel(writer, sheet_name="BaseTag_SHAP", index=False)
        all_scores_df.sort_values("Root_Cause_Score", ascending=False).to_excel(writer, sheet_name="All_Cause_Scores", index=False)
        direct_causes_df.head(config["top_n_direct"]).to_excel(writer, sheet_name="Direct_Causes", index=False)
        indirect_causes_df.head(config["top_n_indirect"]).to_excel(writer, sheet_name="Indirect_Causes", index=False)
        top_root_df.to_excel(writer, sheet_name="Top_Root_Causes", index=False)
        propagation_df.to_excel(writer, sheet_name="Propagation_Paths", index=False)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

    print("Done.")
    print(f"Output saved to: {config['output_file']}")
    print("\nTop root causes:")
    print(top_root_df[[
        "X_Tag", "Cause_Type", "Root_Cause_Score",
        "Direct_Cause_Score", "Indirect_Cause_Score",
        "Base_SHAP", "Granger_p_value", "Lag_Correlation"
    ]])

    return {
        "work_df": work_df,
        "all_scores_df": all_scores_df,
        "direct_causes_df": direct_causes_df,
        "indirect_causes_df": indirect_causes_df,
        "top_root_df": top_root_df,
        "propagation_df": propagation_df,
        "summary_df": summary_df
    }


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    results = generic_root_cause_pipeline(CONFIG)