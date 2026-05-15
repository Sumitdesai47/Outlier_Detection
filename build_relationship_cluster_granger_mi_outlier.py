"""
WITHOUT CAUSAL MATRIX — RELATIONSHIP-CLUSTER TRUE OUTLIER DETECTION

This script improves the earlier threshold-only / relaxed-cluster logic by learning
normal tag relationships from the data itself and then detecting only the rows where
an individual tag behaves differently from its expected cluster/peer pattern.

Relationship signals used:
1) Cross-correlation with lag search — identifies lagged peer movement.
2) Granger causality — adds directional evidence where one tag helps predict another.
3) Mutual information — captures non-linear dependency.
4) Cluster z-score consistency — compares each tag's z-score with its learned peer group.
5) Ridge residual model — predicts target z-score from related lagged peers and scores residuals.

Important: this is still a WITHOUT-CAUSAL-MATRIX statistical model. Granger is statistical
predictability, not engineering causality.

Example:
python build_relationship_cluster_granger_mi_outlier.py \
  --data_file "Multi_X_Multi_Y_Correct_Data.xlsx" \
  --output_excel "relationship_cluster_granger_mi_outlier.xlsx" \
  --output_zip "relationship_cluster_granger_mi_full_csv.zip"

Optional benchmark comparison:
python build_relationship_cluster_granger_mi_outlier.py \
  --data_file "Multi_X_Multi_Y_Correct_Data.xlsx" \
  --benchmark_file "context_aware_outlier_results.xlsx" \
  --benchmark_sheet_name "All_Results"
"""

from __future__ import annotations

import argparse
import math
import os
import warnings
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

try:
    from sklearn.feature_selection import mutual_info_regression
except Exception:  # pragma: no cover
    mutual_info_regression = None

try:
    from statsmodels.tsa.stattools import grangercausalitytests
except Exception:  # pragma: no cover
    grangercausalitytests = None

try:
    import networkx as nx
except Exception:  # pragma: no cover
    nx = None


DEFAULTS = {
    "data_file": "Multi_X_Multi_Y_Correct_Data.xlsx",
    "data_sheet_name": None,
    "timestamp_col": "Timestamp",
    "benchmark_file": "",
    "benchmark_sheet_name": "All_Results",
    "output_excel": "relationship_cluster_granger_mi_outlier.xlsx",
    "output_zip": "relationship_cluster_granger_mi_full_csv.zip",
    "clean_window_days": 150,
    "clean_step_days": 10,
    "min_clean_rows": 90,
    "max_lag": 3,
    "top_k_peers": 7,
    "min_relation_score": 0.38,
    "cluster_edge_score": 0.44,
    "max_cluster_size": 10,
    "value_z_min": 2.45,
    "value_z_max": 3.60,
    "mismatch_z_min": 2.05,
    "mismatch_z_max": 3.40,
    "residual_z_min": 2.15,
    "residual_z_max": 3.70,
    "clean_quantile": 0.992,
    "support_fraction_limit": 0.52,
    "isolated_group_fraction_limit": 0.34,
    "cluster_shift_fraction_limit": 0.45,
    "pattern_drift_fraction_limit": 0.46,
    "ridge_alpha": 0.8,
    "write_full_results": 0,
    "eps": 1e-9,
}


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    return out


def find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    norm = {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}
    for cand in candidates:
        key = cand.lower().replace(" ", "_")
        if key in norm:
            return norm[key]
    return None


def normalize_timestamp(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def robust_center_scale(s: pd.Series, eps: float = 1e-9) -> Tuple[float, float]:
    x = pd.to_numeric(s, errors="coerce").dropna()
    if len(x) == 0:
        return np.nan, np.nan
    med = float(x.median())
    mad = float((x - med).abs().median())
    scale = 1.4826 * mad
    if not np.isfinite(scale) or scale < eps:
        scale = float(x.std(ddof=0))
    if not np.isfinite(scale) or scale < eps:
        scale = eps
    return med, scale


def safe_divide(a, b):
    if b is None or b == 0 or pd.isna(b):
        return np.nan
    return a / b


def clip_num(x, low, high):
    if pd.isna(x) or not np.isfinite(x):
        return low
    return float(min(max(x, low), high))


def weighted_average(values: np.ndarray, weights: np.ndarray) -> float:
    m = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if m.sum() == 0:
        return np.nan
    return float(np.sum(values[m] * weights[m]) / np.sum(weights[m]))


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    m = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if m.sum() == 0:
        return np.nan
    v = values[m]
    w = weights[m]
    idx = np.argsort(v)
    v = v[idx]
    w = w[idx]
    cw = np.cumsum(w)
    cutoff = 0.5 * np.sum(w)
    return float(v[np.searchsorted(cw, cutoff)])


# ============================================================
# DATA LOADING
# ============================================================

def read_excel(path: str, sheet_name=None) -> pd.DataFrame:
    if sheet_name is None or str(sheet_name).strip() == "":
        return pd.read_excel(path)
    return pd.read_excel(path, sheet_name=sheet_name)


def load_data(path: str, sheet_name, timestamp_col: str) -> Tuple[pd.DataFrame, str, List[str]]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    df = clean_column_names(read_excel(path, sheet_name))
    ts = find_column(df, [timestamp_col, "Timestamp", "Time", "Date", "DateTime"])
    if ts is None:
        raise ValueError("Timestamp column not found")
    df[ts] = normalize_timestamp(df[ts])
    df = df.dropna(subset=[ts]).sort_values(ts).reset_index(drop=True)
    tag_cols = []
    for c in df.columns:
        if c == ts:
            continue
        x = pd.to_numeric(df[c], errors="coerce")
        if x.notna().sum() >= 10:
            df[c] = x
            tag_cols.append(c)
    if len(tag_cols) < 2:
        raise ValueError("At least two numeric tag columns are required")
    return df, ts, tag_cols


# ============================================================
# CLEAN PERIOD DETECTION
# ============================================================

def detect_clean_period(df: pd.DataFrame, ts: str, tags: List[str], cfg: Dict) -> Tuple[pd.DataFrame, pd.DataFrame, Tuple[pd.Timestamp, pd.Timestamp]]:
    x = df[tags].apply(pd.to_numeric, errors="coerce")
    z = pd.DataFrame(index=df.index)
    dz = pd.DataFrame(index=df.index)
    for c in tags:
        med, sc = robust_center_scale(x[c], cfg["eps"])
        z[c] = (x[c] - med) / sc
        diff = x[c].diff()
        dmed, dsc = robust_center_scale(diff, cfg["eps"])
        dz[c] = (diff - dmed) / dsc

    bad_frac = (z.abs() > 3.2).mean(axis=1)
    extreme_frac = (z.abs() > 4.2).mean(axis=1)
    delta_bad_frac = (dz.abs() > 3.2).mean(axis=1)
    missing_frac = x.isna().mean(axis=1)
    row_score = 0.55 * bad_frac + 0.20 * extreme_frac + 0.20 * delta_bad_frac + 0.05 * missing_frac

    daily = pd.DataFrame({
        "Timestamp": df[ts],
        "Bad_Tag_Fraction_GlobalZ": bad_frac,
        "Extreme_Tag_Fraction_GlobalZ": extreme_frac,
        "Spike_Tag_Fraction_DeltaZ": delta_bad_frac,
        "Missing_Fraction": missing_frac,
        "Clean_Row_Score": row_score,
        "Clean_Row_Flag": row_score <= np.nanquantile(row_score, 0.35),
    })

    candidates = []
    n = len(df)
    base_window = int(cfg["clean_window_days"])
    window_sizes = sorted(set([max(cfg["min_clean_rows"], int(base_window * f)) for f in [0.75, 1.0, 1.35]]))
    step = int(cfg["clean_step_days"])

    for w in window_sizes:
        if w > n:
            continue
        for start in range(0, n - w + 1, step):
            end = start + w
            sub = daily.iloc[start:end]
            stable_rows = int(sub["Clean_Row_Flag"].sum())
            score = float(sub["Clean_Row_Score"].mean())
            score_p90 = float(sub["Clean_Row_Score"].quantile(0.90))
            bad_mean = float(sub["Bad_Tag_Fraction_GlobalZ"].mean())
            spike_mean = float(sub["Spike_Tag_Fraction_DeltaZ"].mean())
            # Penalize unstable windows but avoid choosing tiny perfect windows.
            final_score = score + 0.20 * score_p90 + 0.10 * bad_mean + 0.05 * spike_mean - 0.00005 * w
            candidates.append({
                "Start_Index": start,
                "End_Index": end - 1,
                "Start_Date": df.loc[start, ts],
                "End_Date": df.loc[end - 1, ts],
                "Window_Rows": w,
                "Stable_Row_Count": stable_rows,
                "Stable_Row_Fraction": safe_divide(stable_rows, w),
                "Avg_Clean_Row_Score": score,
                "P90_Clean_Row_Score": score_p90,
                "Avg_Bad_Tag_Fraction": bad_mean,
                "Avg_Spike_Tag_Fraction": spike_mean,
                "Final_Window_Score": final_score,
            })

    cand = pd.DataFrame(candidates)
    if cand.empty:
        raise ValueError("Could not build clean period candidates")
    cand = cand.sort_values(["Final_Window_Score", "Avg_Bad_Tag_Fraction", "Avg_Spike_Tag_Fraction"]).reset_index(drop=True)
    best = cand.iloc[0]
    start_i, end_i = int(best["Start_Index"]), int(best["End_Index"])
    clean_mask = pd.Series(False, index=df.index)
    clean_mask.iloc[start_i:end_i + 1] = True
    return clean_mask, cand, (df.loc[start_i, ts], df.loc[end_i, ts])


# ============================================================
# Z-SCORE PROFILE FROM CLEAN PERIOD
# ============================================================

def build_reference_profile(df: pd.DataFrame, tags: List[str], clean_mask: pd.Series, cfg: Dict) -> pd.DataFrame:
    rows = []
    for c in tags:
        s = pd.to_numeric(df.loc[clean_mask, c], errors="coerce").dropna()
        mean = float(s.mean())
        std = float(s.std(ddof=0))
        med, mad_scale = robust_center_scale(s, cfg["eps"])
        q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
        iqr_scale = (q3 - q1) / 1.349 if q3 > q1 else std
        # Important: do not let clean-period std become too relaxed.
        scale_candidates = [v for v in [std, mad_scale, iqr_scale] if np.isfinite(v) and v > cfg["eps"]]
        if len(scale_candidates) == 0:
            scale = cfg["eps"]
        else:
            std_cap = 1.35 * max(mad_scale, iqr_scale, cfg["eps"])
            scale = min(std if std > cfg["eps"] else std_cap, std_cap)
            scale = max(scale, 0.75 * mad_scale, cfg["eps"])
        rows.append({
            "Tag": c,
            "Clean_Mean": mean,
            "Clean_Std_Raw": std,
            "Clean_Median": med,
            "Clean_MAD_Scale": mad_scale,
            "Clean_IQR_Scale": iqr_scale,
            "Clean_Effective_Std": scale,
            "Clean_Min": float(s.min()),
            "Clean_Max": float(s.max()),
            "Clean_Q01": float(s.quantile(0.01)),
            "Clean_Q05": float(s.quantile(0.05)),
            "Clean_Q95": float(s.quantile(0.95)),
            "Clean_Q99": float(s.quantile(0.99)),
            "Clean_Rows": int(s.shape[0]),
        })
    return pd.DataFrame(rows)


def compute_z_matrix(df: pd.DataFrame, tags: List[str], ref: pd.DataFrame) -> pd.DataFrame:
    z = pd.DataFrame(index=df.index)
    ref_idx = ref.set_index("Tag")
    for c in tags:
        mean = ref_idx.loc[c, "Clean_Mean"]
        scale = ref_idx.loc[c, "Clean_Effective_Std"]
        med = ref_idx.loc[c, "Clean_Median"]
        mad_scale = ref_idx.loc[c, "Clean_MAD_Scale"]
        std_z = (pd.to_numeric(df[c], errors="coerce") - mean) / scale
        robust_z = (pd.to_numeric(df[c], errors="coerce") - med) / mad_scale
        z[c] = 0.55 * std_z + 0.45 * robust_z
    return z.clip(-20, 20)


# ============================================================
# RELATIONSHIP LEARNING
# ============================================================

def best_cross_corr(x: pd.Series, y: pd.Series, max_lag: int) -> Tuple[float, int, float]:
    """Return best corr, lag and zero-lag corr. Positive lag means y(t-lag) aligns with x(t)."""
    best_r = np.nan
    best_lag = 0
    zero_r = x.corr(y)
    for lag in range(-max_lag, max_lag + 1):
        yy = y.shift(lag)
        valid = x.notna() & yy.notna()
        if valid.sum() < 30:
            continue
        r = x[valid].corr(yy[valid])
        if pd.isna(r):
            continue
        if pd.isna(best_r) or abs(r) > abs(best_r):
            best_r = float(r)
            best_lag = int(lag)
    if pd.isna(best_r):
        best_r = 0.0
    if pd.isna(zero_r):
        zero_r = 0.0
    return float(best_r), int(best_lag), float(zero_r)


def mi_score(x: pd.Series, y: pd.Series) -> float:
    """Fast non-linear dependence score using histogram mutual information.
    This avoids running sklearn MI thousands of times on wide tag sets.
    """
    valid = x.notna() & y.notna()
    if valid.sum() < 40:
        return 0.0
    xv = x[valid].astype(float).values
    yv = y[valid].astype(float).values
    if np.nanstd(xv) < 1e-9 or np.nanstd(yv) < 1e-9:
        return 0.0
    try:
        c_xy = np.histogram2d(xv, yv, bins=14)[0]
        total = np.sum(c_xy)
        if total <= 0:
            return 0.0
        pxy = c_xy / total
        px = pxy.sum(axis=1)
        py = pxy.sum(axis=0)
        denom = px[:, None] * py[None, :]
        nz = (pxy > 0) & (denom > 0)
        mi = np.sum(pxy[nz] * np.log(pxy[nz] / denom[nz]))
        return float(max(mi, 0.0))
    except Exception:
        return 0.0

def granger_min_p(target: pd.Series, source: pd.Series, max_lag: int) -> Tuple[float, int]:
    """Statsmodels tests if second column causes first column."""
    if grangercausalitytests is None:
        return np.nan, 0
    data = pd.concat([target, source], axis=1).dropna()
    if data.shape[0] < max(60, max_lag * 15):
        return np.nan, 0
    if data.iloc[:, 0].std() < 1e-9 or data.iloc[:, 1].std() < 1e-9:
        return np.nan, 0
    try:
        res = grangercausalitytests(data, maxlag=max_lag, verbose=False)
        best_p = 1.0
        best_lag = 0
        for lag, output in res.items():
            p = output[0]["ssr_ftest"][1]
            if p < best_p:
                best_p = float(p)
                best_lag = int(lag)
        return best_p, best_lag
    except Exception:
        return np.nan, 0


def build_relationships(z: pd.DataFrame, tags: List[str], clean_mask: pd.Series, cfg: Dict) -> pd.DataFrame:
    """Build directed relationship table efficiently.

    Cross-correlation and MI are computed for all pairs. Granger is computed only
    for top preliminary candidates per target to keep runtime practical.
    """
    zc = z.loc[clean_mask, tags].reset_index(drop=True)

    prelim_rows = []
    mi_raw = {}
    cc_cache = {}

    # Unordered MI, directed cross-correlation cache.
    for target in tags:
        for source in tags:
            if target == source:
                continue
            cc, lag, zero = best_cross_corr(zc[target], zc[source], int(cfg["max_lag"]))
            cc_cache[(target, source)] = (cc, lag, zero)
            key = tuple(sorted([target, source]))
            if key not in mi_raw:
                mi_raw[key] = mi_score(zc[target], zc[source])

    mi_vals = np.array([v for v in mi_raw.values() if np.isfinite(v)])
    mi_hi = float(np.nanquantile(mi_vals, 0.95)) if len(mi_vals) else 1.0
    if mi_hi <= 0 or not np.isfinite(mi_hi):
        mi_hi = 1.0

    # Preliminary score without Granger.
    for target in tags:
        for source in tags:
            if target == source:
                continue
            cc, lag, zero = cc_cache[(target, source)]
            mi_norm = min(1.0, max(0.0, mi_raw.get(tuple(sorted([target, source])), 0.0) / mi_hi))
            prelim_score = 0.68 * abs(cc) + 0.32 * mi_norm
            prelim_rows.append({
                "Target_Tag": target,
                "Source_Tag": source,
                "Best_CrossCorr": cc,
                "Best_Lag_Source_to_Target": lag,
                "Zero_Lag_Corr": zero,
                "Corr_Sign": 1 if cc >= 0 else -1,
                "Mutual_Info_Raw": mi_raw.get(tuple(sorted([target, source])), 0.0),
                "Mutual_Info_Norm": mi_norm,
                "Prelim_Score": prelim_score,
            })
    pre = pd.DataFrame(prelim_rows)

    # Run Granger only for top candidates per target.
    granger_candidates = set()
    for target, g in pre.groupby("Target_Tag"):
        for _, r in g.sort_values("Prelim_Score", ascending=False).head(max(6, int(cfg["top_k_peers"]) + 2)).iterrows():
            if r["Prelim_Score"] >= 0.20 or abs(r["Best_CrossCorr"]) >= 0.20:
                granger_candidates.add((r["Target_Tag"], r["Source_Tag"]))

    p_cache = {}
    for idx, (target, source) in enumerate(granger_candidates, 1):
        p_val, g_lag = granger_min_p(zc[target], zc[source], int(cfg["max_lag"]))
        p_cache[(target, source)] = (p_val, g_lag)

    rows = []
    for _, r in pre.iterrows():
        key = (r["Target_Tag"], r["Source_Tag"])
        p_val, g_lag = p_cache.get(key, (1.0, 0))
        g_strength = 0.0
        if np.isfinite(p_val) and p_val > 0:
            g_strength = min(1.0, max(0.0, -math.log10(p_val) / 5.0))
        relation_score = 0.50 * abs(float(r["Best_CrossCorr"])) + 0.25 * float(r["Mutual_Info_Norm"]) + 0.25 * g_strength
        rows.append({
            "Target_Tag": r["Target_Tag"],
            "Source_Tag": r["Source_Tag"],
            "Best_CrossCorr": r["Best_CrossCorr"],
            "Best_Lag_Source_to_Target": r["Best_Lag_Source_to_Target"],
            "Zero_Lag_Corr": r["Zero_Lag_Corr"],
            "Corr_Sign": r["Corr_Sign"],
            "Mutual_Info_Raw": r["Mutual_Info_Raw"],
            "Mutual_Info_Norm": r["Mutual_Info_Norm"],
            "Granger_Min_P_Source_to_Target": p_val,
            "Granger_Best_Lag": g_lag,
            "Granger_Strength_Norm": g_strength,
            "Relation_Score": relation_score,
            "Selected_For_Model": False,
        })

    rel = pd.DataFrame(rows).sort_values(["Target_Tag", "Relation_Score"], ascending=[True, False]).reset_index(drop=True)
    selected_idx = []
    for target, g in rel.groupby("Target_Tag"):
        g2 = g[g["Relation_Score"] >= cfg["min_relation_score"]].head(cfg["top_k_peers"])
        if len(g2) < min(3, cfg["top_k_peers"]):
            g2 = g.head(cfg["top_k_peers"])
        selected_idx.extend(g2.index.tolist())
    rel.loc[selected_idx, "Selected_For_Model"] = True
    return rel


# ============================================================
# CLUSTERING
# ============================================================

def build_clusters(tags: List[str], rel: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    # Undirected best relation per pair.
    pair_rows = []
    for (a, b), g in rel.assign(Pair=lambda d: d.apply(lambda r: tuple(sorted([r["Target_Tag"], r["Source_Tag"]])), axis=1)).groupby("Pair"):
        if a == b:
            continue
        best = g.sort_values("Relation_Score", ascending=False).iloc[0]
        pair_rows.append({"Tag_A": a, "Tag_B": b, "Undirected_Relation_Score": float(best["Relation_Score"]), "Best_Abs_Corr": abs(float(best["Best_CrossCorr"])), "Best_MI": float(best["Mutual_Info_Norm"])})
    pair_df = pd.DataFrame(pair_rows)

    if nx is None:
        # fallback: put all non-isolated by connected components based on edge threshold.
        parent = {t: t for t in tags}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        def union(a,b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
        for _, r in pair_df.iterrows():
            if r["Undirected_Relation_Score"] >= cfg["cluster_edge_score"]:
                union(r["Tag_A"], r["Tag_B"])
        comps = {}
        for t in tags:
            comps.setdefault(find(t), []).append(t)
        clusters = list(comps.values())
    else:
        G = nx.Graph()
        for t in tags:
            G.add_node(t)
        # Keep top relation edges to avoid one giant connected cluster.
        keep_edges = set()
        for t in tags:
            cand = pair_df[(pair_df["Tag_A"] == t) | (pair_df["Tag_B"] == t)].sort_values("Undirected_Relation_Score", ascending=False).head(5)
            for _, r in cand.iterrows():
                if r["Undirected_Relation_Score"] >= cfg["cluster_edge_score"] or r["Best_Abs_Corr"] >= 0.55:
                    keep_edges.add(tuple(sorted([r["Tag_A"], r["Tag_B"]])))
        for a, b in keep_edges:
            score = float(pair_df[((pair_df["Tag_A"] == a) & (pair_df["Tag_B"] == b)) | ((pair_df["Tag_A"] == b) & (pair_df["Tag_B"] == a))]["Undirected_Relation_Score"].max())
            G.add_edge(a, b, weight=score)
        try:
            comms = list(nx.algorithms.community.greedy_modularity_communities(G, weight="weight"))
            clusters = [list(c) for c in comms]
        except Exception:
            clusters = [list(c) for c in nx.connected_components(G)]

    # Split very large clusters by highest internal relation to keep groups useful.
    final_clusters = []
    max_size = int(cfg["max_cluster_size"])
    for cl in clusters:
        cl = sorted(cl)
        if len(cl) <= max_size:
            final_clusters.append(cl)
        else:
            # Simple split: sort by average relation and chunk. The relationship table is still used for per-tag peers.
            avg_scores = []
            for t in cl:
                s = pair_df[((pair_df["Tag_A"] == t) | (pair_df["Tag_B"] == t)) & (pair_df["Tag_A"].isin(cl)) & (pair_df["Tag_B"].isin(cl))]["Undirected_Relation_Score"].mean()
                avg_scores.append((t, 0 if pd.isna(s) else s))
            ordered = [t for t, _ in sorted(avg_scores, key=lambda x: x[1], reverse=True)]
            for i in range(0, len(ordered), max_size):
                final_clusters.append(sorted(ordered[i:i+max_size]))

    # Ensure no singleton is left without a group if it has any relationship.
    # Singletons are attached to the best cluster if relation is moderately high.
    singles = [cl[0] for cl in final_clusters if len(cl) == 1]
    non_singles = [cl for cl in final_clusters if len(cl) > 1]
    for s in singles:
        best_cluster_i, best_score = None, -1
        for idx, cl in enumerate(non_singles):
            score = pair_df[((pair_df["Tag_A"] == s) & (pair_df["Tag_B"].isin(cl))) | ((pair_df["Tag_B"] == s) & (pair_df["Tag_A"].isin(cl)))]["Undirected_Relation_Score"].max()
            if pd.notna(score) and score > best_score:
                best_score = float(score)
                best_cluster_i = idx
        if best_cluster_i is not None and best_score >= cfg["min_relation_score"] and len(non_singles[best_cluster_i]) < max_size:
            non_singles[best_cluster_i].append(s)
        else:
            non_singles.append([s])
    final_clusters = [sorted(cl) for cl in non_singles]

    rows = []
    for i, cl in enumerate(final_clusters, 1):
        for t in cl:
            rows.append({"Cluster_ID": f"G{i:02d}", "Tag": t, "Cluster_Size": len(cl), "Cluster_Tags": ", ".join(cl)})
    return pd.DataFrame(rows).sort_values(["Cluster_ID", "Tag"]).reset_index(drop=True)


# ============================================================
# RIDGE MODEL + PEER CONSISTENCY
# ============================================================

def align_source_z(z: pd.DataFrame, source: str, lag: int) -> pd.Series:
    return z[source].shift(int(lag))


def fit_ridge_predict(X_train: pd.DataFrame, y_train: pd.Series, X_all: pd.DataFrame, alpha: float, eps: float) -> Tuple[pd.Series, float]:
    valid = y_train.notna() & X_train.notna().mean(axis=1).ge(0.70)
    Xtr = X_train.loc[valid].copy()
    ytr = y_train.loc[valid].copy()
    if len(ytr) < 40 or Xtr.shape[1] == 0:
        return pd.Series(np.nan, index=X_all.index), np.nan
    med = Xtr.median(numeric_only=True)
    Xtr = Xtr.fillna(med)
    Xa = X_all.fillna(med)
    mu = Xtr.mean(axis=0)
    sd = Xtr.std(axis=0).replace(0, 1.0).fillna(1.0)
    Xs = (Xtr - mu) / sd
    Xas = (Xa - mu) / sd
    X = np.column_stack([np.ones(len(Xs)), Xs.values])
    Xa2 = np.column_stack([np.ones(len(Xas)), Xas.values])
    I = np.eye(X.shape[1])
    I[0, 0] = 0.0
    try:
        beta = np.linalg.solve(X.T @ X + alpha * I, X.T @ ytr.values)
    except np.linalg.LinAlgError:
        beta = np.linalg.pinv(X.T @ X + alpha * I) @ X.T @ ytr.values
    pred_train = X @ beta
    pred_all = Xa2 @ beta
    ss_res = float(np.sum((ytr.values - pred_train) ** 2))
    ss_tot = float(np.sum((ytr.values - np.mean(ytr.values)) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > eps else np.nan
    return pd.Series(pred_all, index=X_all.index), r2


def build_thresholds_for_tag(clean_vals: pd.DataFrame, cfg: Dict) -> Dict[str, float]:
    q = float(cfg["clean_quantile"])
    value_thr = clip_num(np.nanquantile(np.abs(clean_vals["Tag_Z"]), q), cfg["value_z_min"], cfg["value_z_max"])
    mismatch_thr = clip_num(np.nanquantile(np.abs(clean_vals["Peer_Mismatch_Z"]), q), cfg["mismatch_z_min"], cfg["mismatch_z_max"])
    resid_thr = clip_num(np.nanquantile(np.abs(clean_vals["Regression_Residual_Z"]), q), cfg["residual_z_min"], cfg["residual_z_max"])
    return {"Value_Z_Threshold": value_thr, "Peer_Mismatch_Threshold": mismatch_thr, "Regression_Residual_Threshold": resid_thr}


def score_all_tags(df: pd.DataFrame, ts: str, tags: List[str], z: pd.DataFrame, clean_mask: pd.Series, rel: pd.DataFrame, clusters: pd.DataFrame, cfg: Dict) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cluster_map = clusters.set_index("Tag")["Cluster_ID"].to_dict()
    rel_sel = rel[rel["Selected_For_Model"]].copy()

    all_frames = []
    threshold_rows = []
    model_rows = []

    for target in tags:
        peers = rel_sel[rel_sel["Target_Tag"] == target].sort_values("Relation_Score", ascending=False).head(cfg["top_k_peers"]).copy()
        if peers.empty:
            # fallback: use top correlations from clean z
            corr = z.loc[clean_mask, tags].corr()[target].drop(target).abs().sort_values(ascending=False).head(cfg["top_k_peers"])
            rows = []
            for source, sc in corr.items():
                cc, lag, zero = best_cross_corr(z.loc[clean_mask, target].reset_index(drop=True), z.loc[clean_mask, source].reset_index(drop=True), cfg["max_lag"])
                rows.append({"Target_Tag": target, "Source_Tag": source, "Relation_Score": abs(cc), "Best_CrossCorr": cc, "Best_Lag_Source_to_Target": lag, "Corr_Sign": 1 if cc >= 0 else -1})
            peers = pd.DataFrame(rows)

        peer_cols = []
        signed_peer_cols = []
        weights = []
        peer_names = []
        relation_desc = []
        for _, r in peers.iterrows():
            source = r["Source_Tag"]
            lag = int(r.get("Best_Lag_Source_to_Target", 0))
            sign = int(r.get("Corr_Sign", 1))
            score = float(r.get("Relation_Score", 0.1))
            aligned = align_source_z(z, source, lag)
            peer_cols.append(aligned.rename(source))
            signed_peer_cols.append((sign * aligned).rename(source))
            weights.append(max(score, 0.05))
            peer_names.append(source)
            relation_desc.append(f"{source}(score={score:.2f},corr={float(r.get('Best_CrossCorr', np.nan)):.2f},lag={lag},sign={sign})")

        if len(peer_cols) == 0:
            continue
        X_all = pd.concat(peer_cols, axis=1)
        X_signed = pd.concat(signed_peer_cols, axis=1)
        weights_np = np.array(weights, dtype=float)

        y_z = z[target]
        pred_z, r2 = fit_ridge_predict(X_all.loc[clean_mask], y_z.loc[clean_mask], X_all, cfg["ridge_alpha"], cfg["eps"])
        if pred_z.isna().all():
            # fallback to sign-adjusted weighted peer expectation
            pred_z = X_signed.apply(lambda row: weighted_average(row.values.astype(float), weights_np), axis=1)
            r2 = np.nan

        peer_expected = X_signed.apply(lambda row: weighted_median(row.values.astype(float), weights_np), axis=1)
        peer_avg = X_signed.apply(lambda row: weighted_average(row.values.astype(float), weights_np), axis=1)
        peer_mismatch = y_z - peer_expected
        reg_resid = y_z - pred_z

        # Scale mismatch/residual using clean period standard deviation of the derived errors.
        m_med, m_scale = robust_center_scale(peer_mismatch.loc[clean_mask], cfg["eps"])
        r_med, r_scale = robust_center_scale(reg_resid.loc[clean_mask], cfg["eps"])
        peer_mismatch_z = (peer_mismatch - m_med) / m_scale
        reg_resid_z = (reg_resid - r_med) / r_scale

        # Peer support: related tags have expected directional movement.
        support_list = []
        opposite_list = []
        active_list = []
        for idx in range(len(df)):
            target_z = y_z.iloc[idx]
            vals = X_signed.iloc[idx].values.astype(float)
            valid = np.isfinite(vals)
            if not np.isfinite(target_z) or valid.sum() == 0:
                support_list.append(np.nan)
                opposite_list.append(np.nan)
                active_list.append(np.nan)
                continue
            abs_target = abs(target_z)
            active_min = max(0.65, 0.30 * abs_target)
            active = valid & (np.abs(vals) >= active_min)
            if active.sum() == 0:
                support_list.append(0.0)
                opposite_list.append(0.0)
                active_list.append(0.0)
                continue
            same = np.sign(vals[active]) == np.sign(target_z)
            support_list.append(float(np.mean(same)))
            opposite_list.append(float(np.mean(~same)))
            active_list.append(float(np.mean(active)))

        tmp = pd.DataFrame({
            "Timestamp": df[ts],
            "Tag": target,
            "Actual_Value": df[target],
            "Cluster_ID": cluster_map.get(target, "Ungrouped"),
            "Tag_Z": y_z,
            "Abs_Tag_Z": y_z.abs(),
            "Peer_Expected_Z": peer_expected,
            "Peer_Avg_Z": peer_avg,
            "Peer_Mismatch": peer_mismatch,
            "Peer_Mismatch_Z": peer_mismatch_z,
            "Predicted_Tag_Z_From_Related_Tags": pred_z,
            "Regression_Residual_Z": reg_resid_z,
            "Peer_Support_Fraction": support_list,
            "Peer_Opposite_Fraction": opposite_list,
            "Peer_Active_Fraction": active_list,
            "Related_Peer_Count": len(peer_names),
            "Related_Tags": ", ".join(peer_names),
            "Relationship_Details": "; ".join(relation_desc),
            "Model_R2_Clean": r2,
        })
        thr = build_thresholds_for_tag(tmp.loc[clean_mask, ["Tag_Z", "Peer_Mismatch_Z", "Regression_Residual_Z"]], cfg)
        for k, v in thr.items():
            tmp[k] = v
        threshold_rows.append({"Tag": target, **thr, "Model_R2_Clean": r2, "Related_Tags": ", ".join(peer_names), "Relationship_Details": "; ".join(relation_desc)})
        model_rows.append({"Tag": target, "Model_R2_Clean": r2, "Related_Peer_Count": len(peer_names), "Related_Tags": ", ".join(peer_names)})
        all_frames.append(tmp)

    all_results = pd.concat(all_frames, ignore_index=True)
    thresholds = pd.DataFrame(threshold_rows)
    model_info = pd.DataFrame(model_rows)

    # Add cluster-level metrics per timestamp.
    cluster_metrics = []
    for (timestamp, cid), g in all_results.groupby(["Timestamp", "Cluster_ID"]):
        cluster_metrics.append({
            "Timestamp": timestamp,
            "Cluster_ID": cid,
            "Cluster_Tag_Count": int(g["Tag"].nunique()),
            "Cluster_Abs_Z_Median": float(g["Abs_Tag_Z"].median()),
            "Cluster_Abs_Z_Max": float(g["Abs_Tag_Z"].max()),
            "Cluster_Shift_Fraction_Z2": float((g["Abs_Tag_Z"] >= 2.0).mean()),
            "Cluster_High_Z_Fraction": float((g["Abs_Tag_Z"] >= g["Value_Z_Threshold"]).mean()),
            "Cluster_Mismatch_Fraction": float(((g["Peer_Mismatch_Z"].abs() >= g["Peer_Mismatch_Threshold"]) | (g["Regression_Residual_Z"].abs() >= g["Regression_Residual_Threshold"])).mean()),
            "Cluster_Avg_Peer_Support": float(g["Peer_Support_Fraction"].mean()),
        })
    cluster_daily = pd.DataFrame(cluster_metrics)
    all_results = all_results.merge(cluster_daily, on=["Timestamp", "Cluster_ID"], how="left")

    # Decision layer
    high_value = all_results["Abs_Tag_Z"] >= all_results["Value_Z_Threshold"]
    high_mismatch = all_results["Peer_Mismatch_Z"].abs() >= all_results["Peer_Mismatch_Threshold"]
    high_resid = all_results["Regression_Residual_Z"].abs() >= all_results["Regression_Residual_Threshold"]
    supported = (all_results["Peer_Support_Fraction"] >= cfg["support_fraction_limit"]) & (all_results["Peer_Active_Fraction"] >= 0.35)
    cluster_shift = all_results["Cluster_Shift_Fraction_Z2"] >= cfg["cluster_shift_fraction_limit"]
    cluster_pattern = all_results["Cluster_Mismatch_Fraction"] >= cfg["pattern_drift_fraction_limit"]
    isolated = all_results["Cluster_Mismatch_Fraction"] <= cfg["isolated_group_fraction_limit"]

    score = (
        30 * (all_results["Abs_Tag_Z"] / all_results["Value_Z_Threshold"]).clip(0, 2)
        + 35 * (all_results["Peer_Mismatch_Z"].abs() / all_results["Peer_Mismatch_Threshold"]).clip(0, 2)
        + 30 * (all_results["Regression_Residual_Z"].abs() / all_results["Regression_Residual_Threshold"]).clip(0, 2)
        + 12 * (1 - all_results["Peer_Support_Fraction"].fillna(0)).clip(0, 1)
    )
    all_results["Severity_Score_0_100"] = score.clip(0, 100).round(1)

    conditions = [
        high_value & high_mismatch & high_resid & (~supported) & isolated,
        ((high_value & (high_mismatch | high_resid)) | (high_mismatch & high_resid & (all_results["Abs_Tag_Z"] >= 1.65))) & (~supported) & isolated,
        cluster_pattern & (cluster_shift | high_mismatch | high_resid),
        cluster_shift & supported & (~high_mismatch) & (~high_resid),
        high_value | high_mismatch | high_resid,
    ]
    choices = [
        "Actual Outlier - Strong",
        "Actual Outlier",
        "Cluster Pattern Drift",
        "Cluster Drift - Supported",
        "Warning - Check",
    ]
    all_results["Final_Class"] = np.select(conditions, choices, default="Normal")
    all_results["Final_Status"] = np.where(all_results["Final_Class"].str.startswith("Actual Outlier"), "Actual Outlier", np.where(all_results["Final_Class"].eq("Normal"), "Normal", "Process Drift / Warning"))

    all_results["Reason"] = np.select([
        all_results["Final_Class"].eq("Actual Outlier - Strong"),
        all_results["Final_Class"].eq("Actual Outlier"),
        all_results["Final_Class"].eq("Cluster Pattern Drift"),
        all_results["Final_Class"].eq("Cluster Drift - Supported"),
        all_results["Final_Class"].eq("Warning - Check"),
    ], [
        "High tag deviation + high peer mismatch + high regression residual; not supported by cluster.",
        "Tag deviation breaks expected peer/cluster relationship and appears isolated within group.",
        "Many tags in the same cluster break their learned relationship pattern together; treat as process/cluster drift.",
        "Tag deviation is supported by related cluster tags; not an isolated outlier.",
        "One or more limits crossed, but evidence is not strong enough for actual isolated outlier.",
    ], default="Within learned clean-period limits and consistent with related tags.")

    # direction labels
    all_results["Tag_Direction"] = np.where(all_results["Tag_Z"] > 0.75, "UP", np.where(all_results["Tag_Z"] < -0.75, "DOWN", "NORMAL"))
    all_results["Peer_Direction"] = np.where(all_results["Peer_Expected_Z"] > 0.75, "UP", np.where(all_results["Peer_Expected_Z"] < -0.75, "DOWN", "NORMAL"))

    return all_results, thresholds, cluster_daily


# ============================================================
# SUMMARIES + BENCHMARK
# ============================================================

def build_summaries(all_results: pd.DataFrame, clusters: pd.DataFrame, rel: pd.DataFrame, ref: pd.DataFrame, clean_start, clean_end, cand: pd.DataFrame, cfg: Dict) -> Dict[str, pd.DataFrame]:
    class_counts = all_results["Final_Class"].value_counts().rename_axis("Final_Class").reset_index(name="Rows")
    total = len(all_results)
    actual = int(all_results["Final_Status"].eq("Actual Outlier").sum())
    normal = int(all_results["Final_Status"].eq("Normal").sum())
    drift_warning = int(all_results["Final_Status"].eq("Process Drift / Warning").sum())

    summary = pd.DataFrame([
        {"Metric": "Method", "Value": "Relationship-cluster outlier detection using cross-correlation, Granger causality, mutual information, z-score consistency, and regression residuals"},
        {"Metric": "Causal Matrix Used", "Value": "No - statistical relationship model only"},
        {"Metric": "Clean Reference Start", "Value": str(pd.to_datetime(clean_start).date())},
        {"Metric": "Clean Reference End", "Value": str(pd.to_datetime(clean_end).date())},
        {"Metric": "Total Tag-Timestamp Checks", "Value": total},
        {"Metric": "Tags", "Value": all_results["Tag"].nunique()},
        {"Metric": "Clusters", "Value": clusters["Cluster_ID"].nunique()},
        {"Metric": "Actual Outlier Rows", "Value": actual},
        {"Metric": "Actual Outlier Rate", "Value": safe_divide(actual, total)},
        {"Metric": "Process Drift / Warning Rows", "Value": drift_warning},
        {"Metric": "Normal Rows", "Value": normal},
        {"Metric": "Max Lag Used for Cross-Correlation/Granger", "Value": cfg["max_lag"]},
        {"Metric": "Top Peers per Tag", "Value": cfg["top_k_peers"]},
        {"Metric": "Main Rule", "Value": "Actual outlier = tag deviates from clean baseline AND breaks expected peer/cluster behavior; group-wide supported movement is not counted as actual outlier."},
    ])

    tag_summary = all_results.groupby("Tag").agg(
        Cluster_ID=("Cluster_ID", "first"),
        Total_Rows=("Tag", "count"),
        Actual_Outlier_Rows=("Final_Status", lambda x: int((x == "Actual Outlier").sum())),
        Drift_Warning_Rows=("Final_Status", lambda x: int((x == "Process Drift / Warning").sum())),
        Normal_Rows=("Final_Status", lambda x: int((x == "Normal").sum())),
        Max_Severity=("Severity_Score_0_100", "max"),
        Avg_Severity=("Severity_Score_0_100", "mean"),
        Avg_Model_R2_Clean=("Model_R2_Clean", "mean"),
        Related_Tags=("Related_Tags", "first"),
    ).reset_index()
    tag_summary["Actual_Outlier_Rate"] = tag_summary["Actual_Outlier_Rows"] / tag_summary["Total_Rows"]
    tag_summary = tag_summary.sort_values(["Actual_Outlier_Rows", "Max_Severity"], ascending=[False, False])

    daily_summary = all_results.groupby("Timestamp").agg(
        Total_Tags=("Tag", "count"),
        Actual_Outlier_Count=("Final_Status", lambda x: int((x == "Actual Outlier").sum())),
        Drift_Warning_Count=("Final_Status", lambda x: int((x == "Process Drift / Warning").sum())),
        Normal_Count=("Final_Status", lambda x: int((x == "Normal").sum())),
        Max_Severity=("Severity_Score_0_100", "max"),
    ).reset_index()
    top_out = all_results[all_results["Final_Status"] == "Actual Outlier"].sort_values(["Timestamp", "Severity_Score_0_100"], ascending=[True, False]).groupby("Timestamp")["Tag"].apply(lambda x: ", ".join(x.head(8))).reset_index(name="Top_Actual_Outlier_Tags")
    daily_summary = daily_summary.merge(top_out, on="Timestamp", how="left").fillna({"Top_Actual_Outlier_Tags": ""})

    plan = pd.DataFrame([
        {"Step": 1, "Action": "Detect clean/reference period", "Details": "Use historical global robust z-score and delta/spike fractions; choose the most stable window."},
        {"Step": 2, "Action": "Create baseline", "Details": "For each tag, calculate clean-period mean, standard deviation, median, MAD scale, IQR scale, and effective std."},
        {"Step": 3, "Action": "Calculate z-score", "Details": "Convert every tag value into clean-reference z-score. This standardizes all tags."},
        {"Step": 4, "Action": "Learn tag relationships", "Details": "Use cross-correlation over lags, Granger causality p-values, and mutual information."},
        {"Step": 5, "Action": "Create clusters", "Details": "Build weighted relationship graph and detect tag communities/groups."},
        {"Step": 6, "Action": "Score cluster consistency", "Details": "For each tag, compare its z-score with the sign-adjusted and lag-adjusted peer z-score pattern."},
        {"Step": 7, "Action": "Regression residual check", "Details": "Predict each target tag z-score from top related tags and score the clean-period residual distribution."},
        {"Step": 8, "Action": "Final classification", "Details": "Actual Outlier only when the tag breaks peer/cluster behavior; supported group movement becomes Cluster Drift, not false positive."},
        {"Step": 9, "Action": "Benchmark comparison", "Details": "If causal-matrix benchmark file is provided, calculate TP, TN, FP, FN, precision, recall, and specificity."},
    ])

    return {
        "Plan_Method": plan,
        "Summary": summary,
        "Class_Counts": class_counts,
        "Clean_Period_Candidates": cand.head(50),
        "Reference_Profile": ref,
        "Clusters": clusters,
        "Relationships_Top": rel.sort_values("Relation_Score", ascending=False).head(1000),
        "Tag_Summary": tag_summary,
        "Daily_Summary": daily_summary,
    }


def load_benchmark(path: str, sheet: str) -> Optional[pd.DataFrame]:
    if not path or not str(path).strip() or not os.path.exists(path):
        return None
    xl = pd.ExcelFile(path)
    use_sheet = sheet if sheet in xl.sheet_names else xl.sheet_names[0]
    return clean_column_names(pd.read_excel(path, sheet_name=use_sheet))


def standardize_benchmark(b: pd.DataFrame) -> pd.DataFrame:
    ts = find_column(b, ["Timestamp", "Time", "Date", "DateTime"])
    tag = find_column(b, ["Tag", "Target_Tag", "Variable", "Column", "Parameter"])
    fc = find_column(b, ["Final_Class", "Class", "Status", "Final Status", "Final_Status"])
    if ts is None or tag is None or fc is None:
        raise ValueError("Benchmark must contain Timestamp, Tag and Final_Class/Status")
    out = b[[ts, tag, fc]].copy()
    out.columns = ["Timestamp", "Tag", "Benchmark_Class"]
    out["Timestamp"] = normalize_timestamp(out["Timestamp"])
    out["Tag"] = out["Tag"].astype(str).str.strip()
    out["Benchmark_Binary"] = np.where(out["Benchmark_Class"].astype(str).str.lower().str.contains("normal|ok|good"), "Normal", "Actual Outlier")
    return out.dropna(subset=["Timestamp", "Tag"])


def compare_to_benchmark(all_results: pd.DataFrame, bench: Optional[pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    if bench is None:
        return {"Benchmark_Comparison": pd.DataFrame([{"Message": "No benchmark file provided. Use --benchmark_file to calculate TP/TN/FP/FN, precision, recall, specificity."}])}
    b = standardize_benchmark(bench)
    a = all_results[["Timestamp", "Tag", "Final_Class", "Final_Status", "Severity_Score_0_100"]].copy()
    a["Model_Binary"] = np.where(a["Final_Status"] == "Actual Outlier", "Actual Outlier", "Normal")
    comp = a.merge(b, on=["Timestamp", "Tag"], how="inner")
    tp = int(((comp["Benchmark_Binary"] == "Actual Outlier") & (comp["Model_Binary"] == "Actual Outlier")).sum())
    tn = int(((comp["Benchmark_Binary"] == "Normal") & (comp["Model_Binary"] == "Normal")).sum())
    fp = int(((comp["Benchmark_Binary"] == "Normal") & (comp["Model_Binary"] == "Actual Outlier")).sum())
    fn = int(((comp["Benchmark_Binary"] == "Actual Outlier") & (comp["Model_Binary"] == "Normal")).sum())
    summary = pd.DataFrame([{
        "Matched_Rows": len(comp), "TP": tp, "TN": tn, "FP": fp, "FN": fn,
        "Accuracy": safe_divide(tp + tn, len(comp)),
        "Precision": safe_divide(tp, tp + fp),
        "Recall": safe_divide(tp, tp + fn),
        "Specificity": safe_divide(tn, tn + fp),
        "F1": safe_divide(2 * tp, 2 * tp + fp + fn),
    }])
    by_tag = []
    for tag, g in comp.groupby("Tag"):
        tp_t = int(((g["Benchmark_Binary"] == "Actual Outlier") & (g["Model_Binary"] == "Actual Outlier")).sum())
        tn_t = int(((g["Benchmark_Binary"] == "Normal") & (g["Model_Binary"] == "Normal")).sum())
        fp_t = int(((g["Benchmark_Binary"] == "Normal") & (g["Model_Binary"] == "Actual Outlier")).sum())
        fn_t = int(((g["Benchmark_Binary"] == "Actual Outlier") & (g["Model_Binary"] == "Normal")).sum())
        by_tag.append({"Tag": tag, "Rows": len(g), "TP": tp_t, "TN": tn_t, "FP": fp_t, "FN": fn_t, "Precision": safe_divide(tp_t, tp_t + fp_t), "Recall": safe_divide(tp_t, tp_t + fn_t), "Specificity": safe_divide(tn_t, tn_t + fp_t)})
    return {"Benchmark_Comparison": summary, "Benchmark_By_Tag": pd.DataFrame(by_tag), "Benchmark_Row_Comparison": comp}


# ============================================================
# EXPORT
# ============================================================

def write_zip(output_zip: str, sheets: Dict[str, pd.DataFrame]):
    temp_dir = output_zip.replace(".zip", "_csv")
    os.makedirs(temp_dir, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for name, df in sheets.items():
            if df is None:
                continue
            fn = f"{name[:90]}.csv"
            path = os.path.join(temp_dir, fn)
            df.to_csv(path, index=False)
            z.write(path, arcname=fn)


def write_excel(output_excel: str, sheets: Dict[str, pd.DataFrame]):
    # Script-side Excel writer for standalone reuse. The generated workbook is compact;
    # full row-level results are always also written to CSV ZIP.
    with pd.ExcelWriter(output_excel, engine="xlsxwriter", datetime_format="yyyy-mm-dd", date_format="yyyy-mm-dd") as writer:
        workbook = writer.book
        header_fmt = workbook.add_format({"bold": True, "font_color": "white", "bg_color": "#1F4E78", "border": 1, "align": "center"})
        title_fmt = workbook.add_format({"bold": True, "font_size": 14, "font_color": "#1F4E78"})
        percent_fmt = workbook.add_format({"num_format": "0.00%"})
        number_fmt = workbook.add_format({"num_format": "0.00"})
        date_fmt = workbook.add_format({"num_format": "yyyy-mm-dd"})
        for name, df in sheets.items():
            if df is None:
                continue
            safe_name = name[:31]
            df.to_excel(writer, sheet_name=safe_name, index=False, startrow=1)
            ws = writer.sheets[safe_name]
            ws.write(0, 0, safe_name, title_fmt)
            if len(df.columns) > 0:
                for col_idx, col in enumerate(df.columns):
                    ws.write(1, col_idx, col, header_fmt)
                    width = min(max(10, min(40, int(max(len(str(col)) + 2, 12)))), 42)
                    if len(df) > 0:
                        sample_len = min(100, len(df))
                        val_width = int(min(40, max([len(str(x)) for x in df[col].head(sample_len).fillna("").values] + [width])))
                        width = max(width, val_width)
                    ws.set_column(col_idx, col_idx, width)
                ws.freeze_panes(2, 0)
                ws.autofilter(1, 0, max(1, len(df) + 1), max(0, len(df.columns) - 1))
                # Format date-like and rate columns.
                for col_idx, col in enumerate(df.columns):
                    lc = str(col).lower()
                    if "date" in lc or "timestamp" in lc:
                        ws.set_column(col_idx, col_idx, 14, date_fmt)
                    elif "rate" in lc or "fraction" in lc or "precision" in lc or "recall" in lc or "specificity" in lc or "accuracy" in lc:
                        ws.set_column(col_idx, col_idx, 14, percent_fmt)
                    elif "score" in lc or "z" in lc or "corr" in lc or "threshold" in lc or "r2" in lc:
                        ws.set_column(col_idx, col_idx, 14, number_fmt)
        # Add a small dashboard chart if sheet exists.
        if "Daily_Summary" in sheets and not sheets["Daily_Summary"].empty:
            dash = writer.sheets.get("Daily_Summary"[:31])
            try:
                chart = workbook.add_chart({"type": "line"})
                daily = sheets["Daily_Summary"]
                cols = {c: i for i, c in enumerate(daily.columns)}
                maxrow = min(len(daily) + 1, 400)
                if "Timestamp" in cols and "Actual_Outlier_Count" in cols:
                    chart.add_series({
                        "name": "Actual Outlier Count",
                        "categories": ["Daily_Summary", 2, cols["Timestamp"], maxrow, cols["Timestamp"]],
                        "values": ["Daily_Summary", 2, cols["Actual_Outlier_Count"], maxrow, cols["Actual_Outlier_Count"]],
                    })
                if "Drift_Warning_Count" in cols:
                    chart.add_series({
                        "name": "Drift / Warning Count",
                        "categories": ["Daily_Summary", 2, cols["Timestamp"], maxrow, cols["Timestamp"]],
                        "values": ["Daily_Summary", 2, cols["Drift_Warning_Count"], maxrow, cols["Drift_Warning_Count"]],
                    })
                chart.set_title({"name": "Daily Outlier / Drift Trend"})
                chart.set_x_axis({"name": "Date"})
                chart.set_y_axis({"name": "Tag Count"})
                dash.insert_chart("H3", chart, {"x_scale": 1.5, "y_scale": 1.2})
            except Exception:
                pass


def main(config: Optional[Dict] = None) -> Dict[str, pd.DataFrame]:
    cfg = DEFAULTS.copy()
    if config:
        cfg.update(config)
    print("Loading data...")
    df, ts, tags = load_data(cfg["data_file"], cfg["data_sheet_name"], cfg["timestamp_col"])
    print(f"Rows={len(df)}, Tags={len(tags)}")

    print("Detecting clean/reference period...")
    clean_mask, candidates, (clean_start, clean_end) = detect_clean_period(df, ts, tags, cfg)
    print(f"Clean period: {pd.to_datetime(clean_start).date()} to {pd.to_datetime(clean_end).date()} ({int(clean_mask.sum())} rows)")

    print("Building clean reference z-score profile...")
    ref = build_reference_profile(df, tags, clean_mask, cfg)
    z = compute_z_matrix(df, tags, ref)

    print("Learning relationships using cross-correlation, Granger causality, and mutual information...")
    rel = build_relationships(z, tags, clean_mask, cfg)

    print("Creating relationship clusters...")
    clusters = build_clusters(tags, rel, cfg)

    print("Scoring tag-vs-cluster behavior and regression residuals...")
    all_results, thresholds, cluster_daily = score_all_tags(df, ts, tags, z, clean_mask, rel, clusters, cfg)

    summaries = build_summaries(all_results, clusters, rel, ref, clean_start, clean_end, candidates, cfg)
    bench = load_benchmark(cfg["benchmark_file"], cfg["benchmark_sheet_name"])
    comp = compare_to_benchmark(all_results, bench)

    final_outliers = all_results[all_results["Final_Status"] == "Actual Outlier"].sort_values("Severity_Score_0_100", ascending=False)
    drift_warnings = all_results[all_results["Final_Status"] == "Process Drift / Warning"].sort_values("Severity_Score_0_100", ascending=False)
    sample_cols = [
        "Timestamp", "Tag", "Cluster_ID", "Actual_Value", "Tag_Z", "Peer_Expected_Z", "Peer_Mismatch_Z", "Regression_Residual_Z",
        "Peer_Support_Fraction", "Cluster_Shift_Fraction_Z2", "Cluster_Mismatch_Fraction", "Final_Class", "Final_Status", "Severity_Score_0_100", "Related_Tags", "Reason"
    ]
    excel_sheets = {
        "Plan_Method": summaries["Plan_Method"],
        "Summary": summaries["Summary"],
        "Class_Counts": summaries["Class_Counts"],
        "Clean_Period_Candidates": summaries["Clean_Period_Candidates"],
        "Clusters": summaries["Clusters"],
        "Relationships_Top": summaries["Relationships_Top"].head(500),
        "Thresholds": thresholds,
        "Tag_Summary": summaries["Tag_Summary"],
        "Daily_Summary": summaries["Daily_Summary"],
        "Cluster_Daily": cluster_daily.head(5000),
        "Actual_Outliers_Top2000": final_outliers[sample_cols].head(2000),
        "Drift_Warnings_Top2000": drift_warnings[sample_cols].head(2000),
        "Benchmark_Comparison": comp["Benchmark_Comparison"],
    }
    if "Benchmark_By_Tag" in comp:
        excel_sheets["Benchmark_By_Tag"] = comp["Benchmark_By_Tag"]

    # Keep default ZIP compact and fast. Full row-level export can be enabled with --write_full_results 1.
    lean_cols = [
        "Timestamp", "Tag", "Actual_Value", "Cluster_ID", "Tag_Z", "Abs_Tag_Z",
        "Peer_Expected_Z", "Peer_Mismatch_Z", "Regression_Residual_Z",
        "Peer_Support_Fraction", "Cluster_Shift_Fraction_Z2", "Cluster_Mismatch_Fraction",
        "Value_Z_Threshold", "Peer_Mismatch_Threshold", "Regression_Residual_Threshold",
        "Final_Class", "Final_Status", "Severity_Score_0_100", "Tag_Direction", "Peer_Direction",
        "Related_Tags", "Reason"
    ]
    lean_results = all_results[[c for c in lean_cols if c in all_results.columns]].copy()
    full_csv = {
        **summaries,
        "Relationships_All": rel,
        "Thresholds": thresholds,
        "Cluster_Daily": cluster_daily,
        "Actual_Outliers": lean_results[lean_results["Final_Status"] == "Actual Outlier"].sort_values("Severity_Score_0_100", ascending=False),
        "Drift_Warnings_Sample_5000": lean_results[lean_results["Final_Status"] == "Process Drift / Warning"].sort_values("Severity_Score_0_100", ascending=False).head(5000),
        "All_Results_Sample_5000": lean_results.sort_values("Severity_Score_0_100", ascending=False).head(5000),
        **comp,
    }
    if int(cfg.get("write_full_results", 0)) == 1:
        full_csv["All_Results_Full"] = lean_results

    print("Writing outputs...")
    write_excel(cfg["output_excel"], excel_sheets)
    write_zip(cfg["output_zip"], full_csv)
    print(f"Generated: {cfg['output_excel']}")
    print(f"Generated: {cfg['output_zip']}")
    return full_csv


def parse_args():
    p = argparse.ArgumentParser()
    for k, v in DEFAULTS.items():
        arg = f"--{k}"
        if isinstance(v, int):
            p.add_argument(arg, type=int, default=v)
        elif isinstance(v, float):
            p.add_argument(arg, type=float, default=v)
        else:
            p.add_argument(arg, default=v)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(vars(args))
