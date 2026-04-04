import pandas as pd
import numpy as np
import warnings
from scipy.stats import ks_2samp, spearmanr
from statsmodels.tsa.stattools import grangercausalitytests
from sklearn.metrics import r2_score, mean_squared_error
from xgboost import XGBRegressor
import shap
from collections import defaultdict, deque

warnings.filterwarnings("ignore")


# =========================================================
# CONFIG
# =========================================================
CONFIG = {
    "data_file": "Multi_X_Multi_Y_Correct_Data.csv",
    "causal_matrix_file": "Tree.xlsx",   # <-- replace with your actual file
    "output_file": "Top10_Drift_RootCause_Loop_Report.xlsx",

    "timestamp_col": "Timestamp",

    # last available date - 2 months
    "lookback_months": 2,

    # smoothing
    "rolling_window": "5D",
    "rolling_min_periods": 1,

    # drift
    "historic_ratio": 0.70,
    "top_n_drift_tags": 10,

    # cause-effect
    "max_analysis_lag": 7,
    "max_model_lag": 3,
    "min_required_rows": 25,

    # output control
    "top_n_root_causes_per_target": 10,
    "max_path_depth": 6,

    # scoring weights
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
# BASIC HELPERS
# =========================================================
def safe_numeric(s):
    return pd.to_numeric(s, errors="coerce")


def normalize_series(s):
    s = pd.Series(s).astype(float)
    if len(s) == 0 or s.nunique(dropna=True) <= 1:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - s.min()) / (s.max() - s.min() + 1e-9)


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

    best_lag = np.nan
    best_corr = np.nan
    best_abs_corr = -999

    for lag in range(1, max_lag + 1):
        corr = temp["x"].shift(lag).corr(temp["y"])
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


# ========================================================
from collections import defaultdict
import pandas as pd

def load_causal_graph(causal_file):
    """
    Build graph from:
    - sheet: Example_Paths
    - column: Path

    Example:
    A → B → C → Y
    becomes:
    A->B, B->C, C->Y
    """

    example_paths_df = pd.read_excel(causal_file, sheet_name="Example_Paths")

    if "Path" not in example_paths_df.columns:
        raise ValueError("Column 'Path' not found in sheet 'Example_Paths'.")

    edges = []

    for path_text in example_paths_df["Path"].dropna():
        # split using the exact arrow used in your file
        tags = [x.strip() for x in str(path_text).split("→") if str(x).strip() != ""]

        if len(tags) < 2:
            continue

        for i in range(len(tags) - 1):
            src = tags[i]
            tgt = tags[i + 1]

            if src != tgt:
                edges.append((src, tgt))

    # remove duplicates
    edges = list(set(edges))

    if len(edges) == 0:
        raise ValueError("No valid edges built from Example_Paths sheet.")

    parents = defaultdict(set)
    children = defaultdict(set)
    nodes = set()

    for src, tgt in edges:
        parents[tgt].add(src)
        children[src].add(tgt)
        nodes.add(src)
        nodes.add(tgt)

    print("Causal graph loaded successfully")
    print("Sheet used: Example_Paths")
    print("Total nodes:", len(nodes))
    print("Total edges:", len(edges))

    return {
        "edges": edges,
        "parents": parents,
        "children": children,
        "nodes": nodes,
        "sheet_used": "Example_Paths",
        "format_used": "path_sheet"
    }
# =========================================================
# GRAPH HELPERS
# =========================================================
def get_all_ancestors(target, parents_map):
    ancestors = set()
    q = deque([target])

    while q:
        node = q.popleft()
        for p in parents_map.get(node, []):
            if p not in ancestors:
                ancestors.add(p)
                q.append(p)

    ancestors.discard(target)
    return ancestors


def get_all_descendants(source, children_map):
    descendants = set()
    q = deque([source])

    while q:
        node = q.popleft()
        for c in children_map.get(node, []):
            if c not in descendants:
                descendants.add(c)
                q.append(c)

    descendants.discard(source)
    return descendants


def find_paths_to_target(source, target, children_map, max_depth=6):
    paths = []

    def dfs(node, path, depth):
        if depth > max_depth:
            return
        if node == target:
            paths.append(path.copy())
            return

        for nxt in children_map.get(node, []):
            if nxt not in path:
                dfs(nxt, path + [nxt], depth + 1)

    dfs(source, [source], 0)
    return paths


def get_target_relations(target, graph):
    direct_causes = sorted(list(graph["parents"].get(target, set())))
    all_ancestors = get_all_ancestors(target, graph["parents"])
    indirect_causes = sorted(list(all_ancestors - set(direct_causes)))

    descendants = sorted(list(get_all_descendants(target, graph["children"])))

    independent_vars = sorted(list(
        set(graph["nodes"]) -
        {target} -
        set(direct_causes) -
        set(indirect_causes) -
        set(descendants)
    ))

    return {
        "target": target,
        "direct_causes": direct_causes,
        "indirect_causes": indirect_causes,
        "independent_vars": independent_vars,
        "descendants": descendants
    }


# =========================================================
# PREPARE DATA
# =========================================================
def load_and_prepare_data(config):
    df = pd.read_csv(config["data_file"])
    ts = config["timestamp_col"]

    df[ts] = pd.to_datetime(df[ts], errors="coerce")
    df = df.dropna(subset=[ts]).sort_values(ts).reset_index(drop=True)

    # convert non-timestamp columns to numeric
    for col in df.columns:
        if col != ts:
            df[col] = safe_numeric(df[col])

    max_date = df[ts].max()
    start_date = max_date - pd.DateOffset(months=config["lookback_months"])

    df = df[(df[ts] >= start_date) & (df[ts] <= max_date)].copy().reset_index(drop=True)

    numeric_cols = [c for c in df.columns if c != ts and pd.api.types.is_numeric_dtype(df[c])]

    # 5-day smoothing
    temp = df[[ts] + numeric_cols].copy().set_index(ts)
    for col in numeric_cols:
        temp[col] = temp[col].rolling(
            config["rolling_window"],
            min_periods=config["rolling_min_periods"]
        ).mean()

    smoothed_df = temp.reset_index()

    return df, smoothed_df, numeric_cols, start_date, max_date


# =========================================================
# DRIFT FOR ALL TAGS
# =========================================================
def calculate_drift_for_all_tags(smoothed_df, numeric_cols, timestamp_col, historic_ratio=0.70):
    split_index = int(len(smoothed_df) * historic_ratio)
    historic = smoothed_df.iloc[:split_index].copy()
    current = smoothed_df.iloc[split_index:].copy()

    rows = []

    for col in numeric_cols:
        ref = historic[col].dropna()
        cur = current[col].dropna()

        drift_info = compute_drift_metrics(ref, cur)
        drift_time = detect_first_drift_time(smoothed_df, col, split_index, timestamp_col)

        ks_stat = drift_info["ks_stat"]
        mean_shift = drift_info["mean_shift"]
        std_shift = drift_info["std_shift"]

        drift_score = (
            (0 if pd.isna(ks_stat) else ks_stat) +
            (0 if pd.isna(mean_shift) else mean_shift) +
            (0 if pd.isna(std_shift) else std_shift)
        )

        rows.append({
            "Tag": col,
            "Drift_Flag": drift_info["drift_flag"],
            "Drift_Start_Time": drift_time,
            "KS_Stat": ks_stat,
            "p_value": drift_info["p_value"],
            "Mean_Shift": mean_shift,
            "Std_Shift": std_shift,
            "Drift_Score": drift_score
        })

    drift_df = pd.DataFrame(rows).sort_values("Drift_Score", ascending=False).reset_index(drop=True)
    return drift_df


# =========================================================
# ROOT CAUSE ANALYSIS FOR ONE TARGET
# =========================================================
def run_root_cause_for_target(work_df, target_col, candidate_causes, graph, config):
    ts = config["timestamp_col"]

    candidate_causes = [c for c in candidate_causes if c in work_df.columns and c != target_col]
    if len(candidate_causes) == 0:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    # target drift info
    split_index = int(len(work_df) * config["historic_ratio"])
    target_drift_time = detect_first_drift_time(work_df, target_col, split_index, ts)

    historic = work_df.iloc[:split_index]
    current = work_df.iloc[split_index:]
    target_drift = compute_drift_metrics(historic[target_col], current[target_col])

    # -------- XGBoost + SHAP --------
    model_df = create_lagged_features(
        work_df[[target_col] + candidate_causes].copy(),
        candidate_causes,
        target_col,
        max_lag=config["max_model_lag"]
    )
    model_df[ts] = work_df[ts].values
    model_df = model_df.dropna().reset_index(drop=True)

    if len(model_df) < config["min_required_rows"]:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    feature_cols = [c for c in model_df.columns if c not in [ts, target_col]]
    X = model_df[feature_cols].copy()
    y = model_df[target_col].copy()

    split_idx_model = int(len(model_df) * 0.80)
    X_train = X.iloc[:split_idx_model]
    X_test = X.iloc[split_idx_model:]
    y_train = y.iloc[:split_idx_model]
    y_test = y.iloc[split_idx_model:]

    model = XGBRegressor(
        n_estimators=300,
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

    # -------- score each candidate --------
    rows = []

    target_rel = get_target_relations(target_col, graph)
    direct_set = set(target_rel["direct_causes"])
    indirect_set = set(target_rel["indirect_causes"])

    for x_col in candidate_causes:
        temp = work_df[[ts, x_col, target_col]].copy().dropna().reset_index(drop=True)
        if len(temp) < config["min_required_rows"]:
            continue

        x_split = int(len(temp) * config["historic_ratio"])
        x_hist = temp.iloc[:x_split][x_col]
        x_cur = temp.iloc[x_split:][x_col]

        x_drift = compute_drift_metrics(x_hist, x_cur)
        x_drift_time = detect_first_drift_time(temp, x_col, x_split, ts)

        best_lag, lag_corr = get_best_leading_lag(
            temp[x_col], temp[target_col], max_lag=config["max_analysis_lag"]
        )

        g_lag, g_p, g_score = granger_score(
            temp[x_col], temp[target_col], max_lag=config["max_analysis_lag"]
        )

        sp_corr, _ = spearmanr(temp[x_col], temp[target_col], nan_policy="omit")
        if pd.isna(sp_corr):
            sp_corr = 0.0

        dlead = drift_lead_score(x_drift_time, target_drift_time, max_gap_days=20)

        paths = find_paths_to_target(
            source=x_col,
            target=target_col,
            children_map=graph["children"],
            max_depth=config["max_path_depth"]
        )

        direct_bonus = 1 if x_col in direct_set else 0
        indirect_bonus = 1 if x_col in indirect_set else 0
        path_bonus = 1 if len(paths) > 0 else 0

        rows.append({
            "Target_Y": target_col,
            "X_Tag": x_col,
            "Relation_Type": "Direct" if x_col in direct_set else ("Indirect" if x_col in indirect_set else "Other"),

            "X_Drift_Flag": x_drift["drift_flag"],
            "X_Drift_Time": x_drift_time,
            "Y_Drift_Flag": target_drift["drift_flag"],
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
            "Drift_Lead_Score": dlead,

            "Direct_Path_Bonus": direct_bonus,
            "Indirect_Path_Bonus": indirect_bonus,
            "Path_Exists_Bonus": path_bonus,
            "Path_Count": len(paths),
            "One_Path": " -> ".join(paths[0]) if len(paths) > 0 else ""
        })

    scores_df = pd.DataFrame(rows)
    if len(scores_df) == 0:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    scores_df = scores_df.merge(base_shap_df, on="X_Tag", how="left")
    scores_df["Base_SHAP"] = scores_df["Base_SHAP"].fillna(0.0)

    w = config["weights"]

    scores_df["SHAP_Score"] = normalize_series(scores_df["Base_SHAP"])
    scores_df["Lag_Score"] = normalize_series(scores_df["Lag_Correlation"].abs())
    scores_df["Corr_Score"] = normalize_series(scores_df["Spearman_Corr"].abs())

    scores_df["Final_Root_Cause_Score"] = (
        w["shap"]       * scores_df["SHAP_Score"].fillna(0) +
        w["granger"]    * scores_df["Granger_Score"].fillna(0) +
        w["lag"]        * scores_df["Lag_Score"].fillna(0) +
        w["drift_lead"] * scores_df["Drift_Lead_Score"].fillna(0) +
        w["path_bonus"] * scores_df["Path_Exists_Bonus"].fillna(0) +
        w["corr"]       * scores_df["Corr_Score"].fillna(0)
    )

    scores_df = scores_df.sort_values("Final_Root_Cause_Score", ascending=False).reset_index(drop=True)

    model_summary_df = pd.DataFrame([{
        "Target_Y": target_col,
        "Rows_Used": len(model_df),
        "Train_R2": train_r2,
        "Test_R2": test_r2,
        "Test_RMSE": test_rmse,
        "Total_Candidate_Causes": len(candidate_causes),
        "Direct_Cause_Count": len(direct_set),
        "Indirect_Cause_Count": len(indirect_set)
    }])

    top_root_df = scores_df.head(config["top_n_root_causes_per_target"]).copy()

    return scores_df, top_root_df, model_summary_df


# =========================================================
# MAIN LOOP
# =========================================================
def main(config):
    # -------- load graph --------
    graph = load_causal_graph(config["causal_matrix_file"])

    # -------- load data --------
    raw_df, smoothed_df, numeric_cols, start_date, end_date = load_and_prepare_data(config)
    ts = config["timestamp_col"]

    # keep only graph nodes available in data
    graph_nodes_in_data = [n for n in graph["nodes"] if n in smoothed_df.columns]
    numeric_cols = [c for c in numeric_cols if c in graph_nodes_in_data]

    # -------- drift for all tags --------
    drift_df = calculate_drift_for_all_tags(
        smoothed_df=smoothed_df[[ts] + numeric_cols].copy(),
        numeric_cols=numeric_cols,
        timestamp_col=ts,
        historic_ratio=config["historic_ratio"]
    )

    top_drift_df = drift_df.head(config["top_n_drift_tags"]).copy()
    top_targets = top_drift_df["Tag"].tolist()

    # -------- relation summary for top drift tags --------
    relation_rows = []
    all_scores_list = []
    all_top_root_list = []
    all_model_summary_list = []
    all_paths_list = []

    for target_y in top_targets:
        rel = get_target_relations(target_y, graph)

        relation_rows.append({
            "Target_Y": target_y,
            "Direct_Causes": ", ".join(rel["direct_causes"]),
            "Indirect_Causes": ", ".join(rel["indirect_causes"]),
            "Independent_Variables": ", ".join(rel["independent_vars"]),
            "Descendants": ", ".join(rel["descendants"]),
            "Direct_Cause_Count": len(rel["direct_causes"]),
            "Indirect_Cause_Count": len(rel["indirect_causes"]),
            "Independent_Count": len(rel["independent_vars"]),
            "Descendant_Count": len(rel["descendants"])
        })

        candidate_causes = rel["direct_causes"] + rel["indirect_causes"]

        # fallback if no ancestors found in graph
        if len(candidate_causes) == 0:
            candidate_causes = [c for c in numeric_cols if c != target_y]

        target_work_cols = [ts, target_y] + [c for c in candidate_causes if c in smoothed_df.columns]
        target_work_df = smoothed_df[target_work_cols].copy().dropna(subset=[target_y]).reset_index(drop=True)

        scores_df, top_root_df, model_summary_df = run_root_cause_for_target(
            work_df=target_work_df,
            target_col=target_y,
            candidate_causes=candidate_causes,
            graph=graph,
            config=config
        )

        if len(scores_df) > 0:
            all_scores_list.append(scores_df)

        if len(top_root_df) > 0:
            all_top_root_list.append(top_root_df)

        if len(model_summary_df) > 0:
            all_model_summary_list.append(model_summary_df)

        # path details for target
        if len(top_root_df) > 0:
            for _, r in top_root_df.iterrows():
                x_tag = r["X_Tag"]
                paths = find_paths_to_target(
                    source=x_tag,
                    target=target_y,
                    children_map=graph["children"],
                    max_depth=config["max_path_depth"]
                )
                if len(paths) == 0:
                    all_paths_list.append({
                        "Target_Y": target_y,
                        "Root_Cause_X": x_tag,
                        "Path": f"{x_tag} -> {target_y}"
                    })
                else:
                    for p in paths[:5]:
                        all_paths_list.append({
                            "Target_Y": target_y,
                            "Root_Cause_X": x_tag,
                            "Path": " -> ".join(p)
                        })

    relations_df = pd.DataFrame(relation_rows)
    all_scores_df = pd.concat(all_scores_list, ignore_index=True) if len(all_scores_list) > 0 else pd.DataFrame()
    all_top_root_df = pd.concat(all_top_root_list, ignore_index=True) if len(all_top_root_list) > 0 else pd.DataFrame()
    all_model_summary_df = pd.concat(all_model_summary_list, ignore_index=True) if len(all_model_summary_list) > 0 else pd.DataFrame()
    all_paths_df = pd.DataFrame(all_paths_list)

    summary_df = pd.DataFrame([{
        "Data_File": config["data_file"],
        "Causal_Matrix_File": config["causal_matrix_file"],
        "Causal_Sheet_Used": graph["sheet_used"],
        "Data_Start_Date_Used": start_date,
        "Data_End_Date_Used": end_date,
        "Lookback_Months": config["lookback_months"],
        "Smoothing": config["rolling_window"],
        "Historic_Ratio": config["historic_ratio"],
        "Total_Numeric_Tags": len(numeric_cols),
        "Top_Drift_Targets_Analysed": len(top_targets)
    }])

    # -------- save excel --------
    with pd.ExcelWriter(config["output_file"], engine="openpyxl") as writer:
        raw_df.to_excel(writer, sheet_name="Raw_Last_2Months_Data", index=False)
        smoothed_df.to_excel(writer, sheet_name="Smoothed_5D_Data", index=False)
        drift_df.to_excel(writer, sheet_name="All_Tag_Drift", index=False)
        top_drift_df.to_excel(writer, sheet_name="Top10_Drifted_Tags", index=False)
        relations_df.to_excel(writer, sheet_name="Target_Relations", index=False)

        if len(all_scores_df) > 0:
            all_scores_df.to_excel(writer, sheet_name="All_Cause_Effect_Scores", index=False)

        if len(all_top_root_df) > 0:
            all_top_root_df.to_excel(writer, sheet_name="Top_Root_Causes", index=False)

        if len(all_model_summary_df) > 0:
            all_model_summary_df.to_excel(writer, sheet_name="Target_Model_Summary", index=False)

        if len(all_paths_df) > 0:
            all_paths_df.to_excel(writer, sheet_name="Propagation_Paths", index=False)

        summary_df.to_excel(writer, sheet_name="Summary", index=False)

    print("Done.")
    print(f"Output saved to: {config['output_file']}")
    print("\nTop drifted tags:")
    print(top_drift_df[["Tag", "Drift_Score", "Drift_Start_Time"]])

    if len(all_top_root_df) > 0:
        print("\nTop root causes across all drifted targets:")
        print(
            all_top_root_df[
                ["Target_Y", "X_Tag", "Relation_Type", "Final_Root_Cause_Score", "One_Path"]
            ].head(20)
        )


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    main(CONFIG)