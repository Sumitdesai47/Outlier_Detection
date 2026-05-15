"""
WITHOUT CAUSAL MATRIX - STRICT CLUSTER MODEL OUTLIER DETECTION

Purpose
-------
This version is designed to reduce both false positives and false negatives by
checking whether a tag breaks away from its learned peer-cluster behaviour.

Main logic
----------
1) Detect a clean/reference period from historical data.
   - Uses shorter best-stable windows so abnormal history does not inflate std.
   - Builds thresholds only from this clean reference.

2) Build tag clusters from clean-period correlation.
   - No causal matrix is used.
   - Peer groups are statistical clusters only.

3) For each cluster, build a PCA/cluster movement model using clean-period z-scores.
   - If all/most tags move together according to the cluster pattern, classify as
     "Cluster Drift - Supported".
   - If one/few tags deviate away from the cluster movement, classify as
     "Actual Outlier".
   - If the cluster has mixed high/low behaviour, classify minority/mismatch tags
     as anomalies or warnings.

4) Output Excel + full CSV ZIP for dashboard integration.

Run
---
python build_cluster_pca_strict_outlier.py \
  --data_file "Multi_X_Multi_Y_Correct_Data.xlsx" \
  --output_file "cluster_pca_strict_true_outlier.xlsx"

Optional benchmark comparison
-----------------------------
python build_cluster_pca_strict_outlier.py \
  --data_file "Multi_X_Multi_Y_Correct_Data.xlsx" \
  --benchmark_file "context_aware_outlier_results.xlsx" \
  --benchmark_sheet_name "All_Results"

Requirements
------------
pip install pandas numpy openpyxl xlsxwriter
"""

from __future__ import annotations

import argparse
import os
import re
import zipfile
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


CONFIG: Dict = {
    "data_file": "Multi_X_Multi_Y_Correct_Data.xlsx",
    "data_sheet_name": None,
    "timestamp_col": "Timestamp",
    "output_file": "cluster_pca_strict_balanced_outlier.xlsx",
    "full_csv_zip": "cluster_pca_strict_balanced_outlier_full_csv.zip",

    # Clean period search. Smaller clean window keeps std limits tight.
    "clean_window_points": 180,
    "clean_window_min_points": 120,
    "clean_window_step": 7,
    "clean_bad_z_limit": 3.0,
    "clean_bad_fraction_weight": 5.0,
    "clean_variability_weight": 2.0,
    "clean_trend_weight": 1.5,
    "clean_top_candidate_count": 30,

    # Reference scales. Use tight but safe scales from clean period.
    "mad_scale_multiplier": 1.35,
    "min_scale_eps": 1e-9,

    # Clustering
    "cluster_abs_corr_threshold": 0.58,
    "cluster_min_size": 3,
    "fallback_peer_count": 6,
    "max_peer_tags": 10,

    # Detection limits - tighter than previous relaxed version.
    "tag_z_limit": 2.50,
    "tag_z_strong_limit": 3.20,
    "residual_z_limit": 2.50,
    "residual_z_strong_limit": 3.20,
    "warning_tag_z_limit": 2.00,
    "warning_residual_z_limit": 2.00,

    # Cluster movement/support rules
    "cluster_move_z": 1.70,
    "cluster_drift_score_limit": 1.80,
    "cluster_support_fraction_min": 0.55,
    "peer_same_direction_min": 0.45,
    "peer_opposite_fraction_limit": 0.35,
    "isolated_peer_high_fraction_max": 0.30,
    "mixed_high_low_fraction_min": 0.25,
    "minority_fraction_max": 0.40,
    "cluster_residual_high_fraction_max_for_tag_outlier": 0.40,

    # Persistence / spike control
    "persistent_points": 2,
    "single_point_strong_residual_z": 3.10,
    "single_point_strong_tag_z": 3.10,

    # Output limits
    "excel_all_results_max_rows": 25000,
    "excel_top_outliers_max_rows": 8000,
}


def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    norm = {re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_"): c for c in df.columns}
    for cand in candidates:
        key = re.sub(r"[^a-z0-9]+", "_", cand.strip().lower()).strip("_")
        if key in norm:
            return norm[key]
    return None


def safe_divide(a, b):
    if b is None or b == 0 or pd.isna(b):
        return np.nan
    return a / b


def robust_center_scale(s: pd.Series, eps: float = 1e-9) -> Tuple[float, float]:
    x = pd.to_numeric(s, errors="coerce").dropna()
    if len(x) == 0:
        return np.nan, np.nan
    med = float(x.median())
    mad = float((x - med).abs().median())
    scale = 1.4826 * mad
    if pd.isna(scale) or scale < eps:
        scale = float(x.std(ddof=1)) if len(x) > 1 else eps
    if pd.isna(scale) or scale < eps:
        scale = eps
    return med, scale


def signed_abs_z(std_z: pd.Series, robust_z: pd.Series) -> pd.Series:
    """Use the larger absolute evidence but keep the std-z direction."""
    sign = np.sign(std_z).replace(0, np.nan).fillna(np.sign(robust_z))
    return sign * np.maximum(std_z.abs(), robust_z.abs())


def load_process_data(config: Dict) -> Tuple[pd.DataFrame, str, List[str]]:
    if not os.path.exists(config["data_file"]):
        raise FileNotFoundError(config["data_file"])
    if config.get("data_sheet_name"):
        df = pd.read_excel(config["data_file"], sheet_name=config["data_sheet_name"])
    else:
        df = pd.read_excel(config["data_file"])
    df = clean_column_names(df)
    ts = find_column(df, [config.get("timestamp_col", "Timestamp"), "Timestamp", "Time", "DateTime", "Date"])
    if ts is None:
        raise ValueError("Timestamp column not found. Please provide --timestamp_col.")
    df[ts] = pd.to_datetime(df[ts], errors="coerce")
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
        raise ValueError("At least two numeric tags are required.")
    return df, ts, tag_cols


def global_robust_z(df: pd.DataFrame, tag_cols: List[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for c in tag_cols:
        med, scale = robust_center_scale(df[c])
        out[c] = (pd.to_numeric(df[c], errors="coerce") - med) / scale
    return out


def detect_clean_period(df: pd.DataFrame, ts: str, tag_cols: List[str], config: Dict) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    gz = global_robust_z(df, tag_cols)
    abs_gz = gz.abs()
    bad_fraction = (abs_gz > config["clean_bad_z_limit"]).mean(axis=1)
    row_median_abs = abs_gz.median(axis=1)
    row_p90_abs = abs_gz.quantile(0.90, axis=1)
    row_score = row_median_abs + 0.6 * row_p90_abs + config["clean_bad_fraction_weight"] * bad_fraction

    candidates = []
    n = len(df)
    window_sizes = sorted(set([
        int(config["clean_window_min_points"]),
        int(config["clean_window_points"]),
        min(240, n),
    ]))
    for w in window_sizes:
        if w < 30 or w > n:
            continue
        for s in range(0, n - w + 1, int(config["clean_window_step"])):
            e = s + w - 1
            sub_z = gz.iloc[s:e+1]
            sub_abs = abs_gz.iloc[s:e+1]
            sub_bad = bad_fraction.iloc[s:e+1]
            sub_score = row_score.iloc[s:e+1]

            # Penalize windows where many tags trend strongly. Clean baseline should be stable.
            trend_vals = []
            x_axis = np.arange(w)
            x_std = np.std(x_axis)
            for c in tag_cols:
                y = pd.to_numeric(sub_z[c], errors="coerce").fillna(0).values
                y_std = np.std(y)
                if y_std < 1e-9:
                    trend_vals.append(0.0)
                else:
                    corr = np.corrcoef(x_axis, y)[0, 1]
                    trend_vals.append(abs(corr) if not np.isnan(corr) else 0.0)
            trend_penalty = float(np.nanmedian(trend_vals))

            # Variability: clean period should have lower per-tag z spread.
            variability = float(sub_z.std(ddof=1).median())
            candidate_score = (
                float(sub_score.mean())
                + config["clean_variability_weight"] * variability
                + config["clean_trend_weight"] * trend_penalty
                + 3.0 * float(sub_bad.mean())
            )
            candidates.append({
                "Start_Timestamp": df.loc[s, ts],
                "End_Timestamp": df.loc[e, ts],
                "Start_Row_Index": s,
                "End_Row_Index": e,
                "Duration_Points": w,
                "Avg_Row_Score": float(sub_score.mean()),
                "Median_Row_Score": float(sub_score.median()),
                "Avg_Bad_Tag_Fraction": float(sub_bad.mean()),
                "Max_Bad_Tag_Fraction": float(sub_bad.max()),
                "Median_Tag_Z_Std": variability,
                "Median_Tag_Trend_AbsCorr": trend_penalty,
                "Clean_Rank_Score": candidate_score,
            })

    cand = pd.DataFrame(candidates)
    if cand.empty:
        raise ValueError("Unable to build clean period candidates.")
    cand = cand.sort_values(["Clean_Rank_Score", "Avg_Bad_Tag_Fraction", "Median_Tag_Z_Std"], ascending=True).reset_index(drop=True)
    selected = cand.iloc[0]
    clean_mask = (df.index >= int(selected["Start_Row_Index"])) & (df.index <= int(selected["End_Row_Index"]))

    daily = pd.DataFrame({
        "Timestamp": df[ts],
        "Bad_Tag_Fraction": bad_fraction,
        "Median_Abs_Global_Z": row_median_abs,
        "P90_Abs_Global_Z": row_p90_abs,
        "Clean_Row_Score": row_score,
        "Selected_Clean_Period": clean_mask,
    })
    return daily, cand.head(config["clean_top_candidate_count"]), pd.Series(clean_mask, index=df.index)


def build_reference_profile(df: pd.DataFrame, tag_cols: List[str], clean_mask: pd.Series, config: Dict) -> pd.DataFrame:
    rows = []
    for tag in tag_cols:
        x = pd.to_numeric(df.loc[clean_mask, tag], errors="coerce").dropna()
        med, mad_scale = robust_center_scale(x, config["min_scale_eps"])
        mean = float(x.mean()) if len(x) else np.nan
        std = float(x.std(ddof=1)) if len(x) > 1 else mad_scale
        if pd.isna(std) or std < config["min_scale_eps"]:
            std = mad_scale
        # Tighter but safe scale: avoids inflated std from abnormal clean windows.
        if pd.notna(mad_scale) and mad_scale > config["min_scale_eps"]:
            z_scale = min(std, mad_scale * config["mad_scale_multiplier"])
        else:
            z_scale = std
        z_scale = max(float(z_scale), config["min_scale_eps"])
        rows.append({
            "Tag": tag,
            "Clean_Count": int(len(x)),
            "Clean_Mean": mean,
            "Clean_Std": std,
            "Clean_Median": med,
            "Clean_MAD_Scale": mad_scale,
            "Final_Z_Scale_Used": z_scale,
            "Clean_Min": float(x.min()) if len(x) else np.nan,
            "Clean_Max": float(x.max()) if len(x) else np.nan,
            "Clean_P01": float(x.quantile(0.01)) if len(x) else np.nan,
            "Clean_P05": float(x.quantile(0.05)) if len(x) else np.nan,
            "Clean_P95": float(x.quantile(0.95)) if len(x) else np.nan,
            "Clean_P99": float(x.quantile(0.99)) if len(x) else np.nan,
        })
    return pd.DataFrame(rows)


def build_z_matrices(df: pd.DataFrame, tag_cols: List[str], ref: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    r = ref.set_index("Tag")
    std_z = pd.DataFrame(index=df.index)
    robust_z_df = pd.DataFrame(index=df.index)
    final_z = pd.DataFrame(index=df.index)
    for tag in tag_cols:
        x = pd.to_numeric(df[tag], errors="coerce")
        mean = r.loc[tag, "Clean_Mean"]
        med = r.loc[tag, "Clean_Median"]
        scale = r.loc[tag, "Final_Z_Scale_Used"]
        mad_scale = r.loc[tag, "Clean_MAD_Scale"]
        if pd.isna(scale) or scale <= 0:
            scale = 1e-9
        if pd.isna(mad_scale) or mad_scale <= 0:
            mad_scale = scale
        sz = (x - mean) / scale
        rz = (x - med) / mad_scale
        std_z[tag] = sz
        robust_z_df[tag] = rz
        final_z[tag] = signed_abs_z(sz, rz)
    return std_z, robust_z_df, final_z


def connected_components(nodes: List[str], edges: Dict[str, List[str]]) -> List[List[str]]:
    seen = set()
    comps = []
    for node in nodes:
        if node in seen:
            continue
        stack = [node]
        seen.add(node)
        comp = []
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in edges.get(u, []):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        comps.append(comp)
    return comps


def build_clusters(df: pd.DataFrame, tag_cols: List[str], clean_mask: pd.Series, config: Dict) -> Tuple[pd.DataFrame, Dict[str, List[str]], pd.DataFrame, pd.DataFrame]:
    clean_data = df.loc[clean_mask, tag_cols].apply(pd.to_numeric, errors="coerce")
    corr = clean_data.corr(method="pearson").fillna(0.0)
    threshold = config["cluster_abs_corr_threshold"]
    edges = {t: [] for t in tag_cols}
    pairs = []
    for i, a in enumerate(tag_cols):
        for b in tag_cols[i+1:]:
            c = float(corr.loc[a, b])
            if abs(c) >= threshold:
                edges[a].append(b)
                edges[b].append(a)
                pairs.append({"Tag_A": a, "Tag_B": b, "Clean_Correlation": c, "Abs_Correlation": abs(c)})
    comps = connected_components(tag_cols, edges)
    cluster_map = {}
    rows = []
    cid_num = 1
    assigned_primary = set()
    for comp in sorted(comps, key=lambda x: (-len(x), x[0])):
        if len(comp) >= config["cluster_min_size"]:
            cid = f"Cluster_{cid_num:02d}"
            tags = sorted(comp)
            cluster_map[cid] = tags
            assigned_primary.update(tags)
            cid_num += 1
    # Fallback cluster for tags without strong component; each gets top peers.
    for tag in tag_cols:
        if tag in assigned_primary:
            continue
        top = corr[tag].drop(index=tag, errors="ignore").abs().sort_values(ascending=False).head(config["fallback_peer_count"]).index.tolist()
        cid = f"Cluster_{cid_num:02d}"
        cluster_map[cid] = sorted(list(dict.fromkeys([tag] + top)))
        assigned_primary.add(tag)
        cid_num += 1
    tag_to_primary = {}
    for cid, tags in cluster_map.items():
        sub = corr.loc[tags, tags].where(~np.eye(len(tags), dtype=bool)) if len(tags) > 1 else pd.DataFrame()
        avg_abs = float(sub.abs().stack().mean()) if len(tags) > 1 else np.nan
        for tag in tags:
            if tag in tag_cols and tag not in tag_to_primary:
                tag_to_primary[tag] = cid
        rows.append({"Cluster_ID": cid, "Cluster_Size": len(tags), "Avg_Abs_Clean_Correlation": avg_abs, "Tags": ", ".join(tags)})
    pair_df = pd.DataFrame(pairs).sort_values("Abs_Correlation", ascending=False) if pairs else pd.DataFrame(columns=["Tag_A", "Tag_B", "Clean_Correlation", "Abs_Correlation"])
    return pd.DataFrame(rows), cluster_map, pair_df, corr


def pca_cluster_model(z_clean: pd.DataFrame, z_all: pd.DataFrame) -> Tuple[pd.Series, pd.DataFrame, pd.Series, pd.Series]:
    """Return cluster_score, expected_z matrix, loadings, explained_variance_ratio."""
    cols = list(z_clean.columns)
    Xc = z_clean.fillna(0.0).values
    Xa = z_all.fillna(0.0).values
    if Xc.shape[1] == 1:
        loading = np.array([1.0])
        score_all = Xa[:, 0]
        expected = Xa.copy()
        evr = 1.0
    else:
        # Center clean z around zero; keep all rows on same center.
        center = np.nanmean(Xc, axis=0)
        Xc0 = Xc - center
        Xa0 = Xa - center
        try:
            _, s, vt = np.linalg.svd(Xc0, full_matrices=False)
            loading = vt[0, :]
            # Normalize loading to max abs 1 so expected z is interpretable.
            max_abs = np.max(np.abs(loading))
            if max_abs < 1e-12:
                loading = np.ones(Xc.shape[1]) / np.sqrt(Xc.shape[1])
            else:
                loading = loading / max_abs
            # Fix orientation for stable reports.
            if np.sum(loading) < 0:
                loading = -loading
            denom = np.dot(loading, loading)
            score_all = (Xa0 @ loading) / denom
            expected = np.outer(score_all, loading)
            evr = float((s[0] ** 2) / np.sum(s ** 2)) if np.sum(s ** 2) > 0 else np.nan
        except np.linalg.LinAlgError:
            loading = np.ones(Xc.shape[1]) / np.sqrt(Xc.shape[1])
            score_all = Xa @ loading / np.dot(loading, loading)
            expected = np.outer(score_all, loading)
            evr = np.nan
    return (
        pd.Series(score_all, index=z_all.index, name="Cluster_PCA_Score"),
        pd.DataFrame(expected, index=z_all.index, columns=cols),
        pd.Series(loading, index=cols, name="PCA_Loading"),
        pd.Series([evr], name="PC1_Explained_Variance_Ratio"),
    )


def run_model(df: pd.DataFrame, ts: str, tag_cols: List[str], clean_mask: pd.Series,
              ref: pd.DataFrame, cluster_df: pd.DataFrame, cluster_map: Dict[str, List[str]], corr: pd.DataFrame,
              config: Dict) -> Dict[str, pd.DataFrame]:
    std_z, robust_z_df, final_z = build_z_matrices(df, tag_cols, ref)
    tag_to_primary = {}
    for _, row in cluster_df.iterrows():
        cid = row["Cluster_ID"]
        for tag in [x.strip() for x in str(row["Tags"]).split(",")]:
            if tag in tag_cols and tag not in tag_to_primary:
                tag_to_primary[tag] = cid
    for tag in tag_cols:
        tag_to_primary.setdefault(tag, cluster_df.iloc[0]["Cluster_ID"])

    all_parts = []
    cluster_daily_parts = []
    peer_rows = []
    loading_rows = []

    for cid, ctags in cluster_map.items():
        ctags = [t for t in ctags if t in tag_cols]
        if not ctags:
            continue
        z_clean = final_z.loc[clean_mask, ctags]
        z_all = final_z[ctags]
        cluster_score, expected_z, loadings, evr_series = pca_cluster_model(z_clean, z_all)
        residual = z_all - expected_z

        # Residual reference and scales from clean period.
        resid_center = residual.loc[clean_mask].median(axis=0)
        resid_mad = (residual.loc[clean_mask] - resid_center).abs().median(axis=0) * 1.4826
        resid_std = residual.loc[clean_mask].std(axis=0, ddof=1)
        resid_scale = pd.Series(index=ctags, dtype=float)
        for t in ctags:
            vals = [v for v in [resid_std.get(t), resid_mad.get(t) * config["mad_scale_multiplier"] if pd.notna(resid_mad.get(t)) else np.nan] if pd.notna(v) and v > config["min_scale_eps"]]
            resid_scale[t] = min(vals) if vals else 1.0

        residual_z = (residual - resid_center) / resid_scale.replace(0, 1.0)

        # Cluster stats per timestamp.
        abs_z = final_z[ctags].abs()
        high_pos_fraction = (final_z[ctags] >= config["cluster_move_z"]).mean(axis=1)
        high_neg_fraction = (final_z[ctags] <= -config["cluster_move_z"]).mean(axis=1)
        high_any_fraction = (abs_z >= config["cluster_move_z"]).mean(axis=1)
        median_abs_z = abs_z.median(axis=1)
        p80_abs_resid_z = residual_z.abs().quantile(0.80, axis=1)
        residual_high_fraction = (residual_z.abs() >= config["residual_z_limit"]).mean(axis=1)
        mixed_flag = (high_pos_fraction >= config["mixed_high_low_fraction_min"]) & (high_neg_fraction >= config["mixed_high_low_fraction_min"])
        cluster_drift_flag = (
            (cluster_score.abs() >= config["cluster_drift_score_limit"]) &
            (high_any_fraction >= config["cluster_support_fraction_min"]) &
            (~mixed_flag) &
            (p80_abs_resid_z < config["residual_z_limit"])
        )
        cluster_daily_parts.append(pd.DataFrame({
            "Timestamp": df[ts],
            "Cluster_ID": cid,
            "Cluster_Size": len(ctags),
            "Cluster_PCA_Score": cluster_score,
            "PC1_Explained_Variance_Ratio": float(evr_series.iloc[0]) if len(evr_series) else np.nan,
            "Cluster_Median_Abs_Z": median_abs_z,
            "Cluster_High_Any_Fraction": high_any_fraction,
            "Cluster_High_Positive_Fraction": high_pos_fraction,
            "Cluster_High_Negative_Fraction": high_neg_fraction,
            "Cluster_P80_Abs_Residual_Z": p80_abs_resid_z,
            "Cluster_Residual_High_Fraction": residual_high_fraction,
            "Mixed_High_Low_Flag": mixed_flag,
            "Cluster_Drift_Supported_Flag": cluster_drift_flag,
            "Cluster_Tags": ", ".join(ctags),
        }))
        for t in ctags:
            loading_rows.append({
                "Cluster_ID": cid,
                "Tag": t,
                "PCA_Loading": float(loadings[t]),
                "PC1_Explained_Variance_Ratio": float(evr_series.iloc[0]) if len(evr_series) else np.nan,
                "Residual_Center_Clean": float(resid_center[t]),
                "Residual_Scale_Used": float(resid_scale[t]),
            })
            # Use top peers from own cluster by abs correlation.
            peers = [p for p in ctags if p != t]
            peers = corr.loc[t, peers].abs().sort_values(ascending=False).head(config["max_peer_tags"]).index.tolist() if peers else []
            peer_rows.append({"Tag": t, "Cluster_ID": cid, "Top_Peers": ", ".join(peers)})

        # Classify each tag in this cluster.
        for t in ctags:
            peers = [p for p in ctags if p != t]
            if peers:
                peer_abs_high_fraction = (final_z[peers].abs() >= config["cluster_move_z"]).mean(axis=1)
                expected_sign = np.sign(expected_z[t]).replace(0, np.nan).fillna(np.sign(cluster_score * loadings[t]))
                tag_sign = np.sign(final_z[t])
                same_cluster_direction = (tag_sign == expected_sign).astype(float)
                # Peers whose actual sign agrees with their own expected PCA sign.
                peer_expected_sign = np.sign(expected_z[peers]).replace(0, np.nan)
                peer_actual_sign = np.sign(final_z[peers])
                peer_same_model_fraction = (peer_actual_sign == peer_expected_sign).mean(axis=1)
                peer_opposite_model_fraction = (peer_actual_sign == -peer_expected_sign).mean(axis=1)
            else:
                peer_abs_high_fraction = pd.Series(0.0, index=df.index)
                same_cluster_direction = pd.Series(0.0, index=df.index)
                peer_same_model_fraction = pd.Series(0.0, index=df.index)
                peer_opposite_model_fraction = pd.Series(0.0, index=df.index)

            tag_abs_z = final_z[t].abs()
            resid_abs_z = residual_z[t].abs()

            # preliminary anomaly signals; persistence handled below.
            # Tag-level outlier should be a minority/local break, not a full-cluster model failure.
            cluster_residual_is_local = residual_high_fraction <= config["cluster_residual_high_fraction_max_for_tag_outlier"]
            prelim_outlier = (
                (tag_abs_z >= config["tag_z_limit"]) &
                (resid_abs_z >= config["residual_z_limit"]) &
                cluster_residual_is_local &
                (
                    (peer_abs_high_fraction <= config["isolated_peer_high_fraction_max"]) |
                    (same_cluster_direction < config["peer_same_direction_min"]) |
                    (peer_opposite_model_fraction >= config["peer_opposite_fraction_limit"]) |
                    mixed_flag
                )
            )
            strong_single_point = (
                (tag_abs_z >= config["single_point_strong_tag_z"]) &
                (resid_abs_z >= config["single_point_strong_residual_z"]) &
                (residual_high_fraction <= 0.60)
            )
            # Persistent if current or neighbouring point is prelim outlier.
            prelim_int = prelim_outlier.astype(int)
            persistent = (prelim_int + prelim_int.shift(1, fill_value=0) + prelim_int.shift(-1, fill_value=0)) >= config["persistent_points"]

            final_status = []
            final_class = []
            severity = []
            for i in range(len(df)):
                tz = float(tag_abs_z.iloc[i]) if pd.notna(tag_abs_z.iloc[i]) else 0.0
                rz = float(resid_abs_z.iloc[i]) if pd.notna(resid_abs_z.iloc[i]) else 0.0
                p_high = float(peer_abs_high_fraction.iloc[i]) if pd.notna(peer_abs_high_fraction.iloc[i]) else 0.0
                same = float(same_cluster_direction.iloc[i]) if pd.notna(same_cluster_direction.iloc[i]) else 0.0
                opp = float(peer_opposite_model_fraction.iloc[i]) if pd.notna(peer_opposite_model_fraction.iloc[i]) else 0.0
                mix = bool(mixed_flag.iloc[i]) if pd.notna(mixed_flag.iloc[i]) else False
                res_high_frac = float(residual_high_fraction.iloc[i]) if pd.notna(residual_high_fraction.iloc[i]) else 0.0
                cdrift = bool(cluster_drift_flag.iloc[i]) if pd.notna(cluster_drift_flag.iloc[i]) else False
                pre = bool(prelim_outlier.iloc[i]) if pd.notna(prelim_outlier.iloc[i]) else False
                pers = bool(persistent.iloc[i]) if pd.notna(persistent.iloc[i]) else False
                strong = bool(strong_single_point.iloc[i]) if pd.notna(strong_single_point.iloc[i]) else False

                if strong and (same < config["peer_same_direction_min"] or p_high <= config["isolated_peer_high_fraction_max"] or mix):
                    cls, stat = "Actual Outlier - Strong Cluster Model Break", "Actual Outlier"
                elif pre and pers:
                    if p_high <= config["isolated_peer_high_fraction_max"]:
                        cls = "Actual Outlier - Isolated From Cluster"
                    elif same < config["peer_same_direction_min"] or opp >= config["peer_opposite_fraction_limit"]:
                        cls = "Actual Outlier - Direction Mismatch"
                    elif mix:
                        cls = "Actual Outlier - Mixed Cluster Behaviour"
                    else:
                        cls = "Actual Outlier - Cluster Residual Break"
                    stat = "Actual Outlier"
                elif cdrift and rz < config["residual_z_limit"]:
                    cls, stat = "Cluster Drift - Supported", "Cluster Drift"
                elif res_high_frac > config["cluster_residual_high_fraction_max_for_tag_outlier"] and (tz >= config["warning_tag_z_limit"] or rz >= config["warning_residual_z_limit"]):
                    cls, stat = "Cluster Pattern Drift - Not Isolated", "Cluster Drift"
                elif (tz >= config["warning_tag_z_limit"] and rz >= config["warning_residual_z_limit"]) or mix:
                    cls, stat = "Warning - Review Cluster Consistency", "Warning"
                else:
                    cls, stat = "Normal", "Normal"

                sev = min(100, 8 * tz + 14 * rz + 12 * max(0, 1 - same) + 10 * opp + (10 if mix else 0))
                if stat == "Cluster Drift":
                    sev *= 0.60
                if stat == "Normal":
                    sev = min(sev, 25)
                final_class.append(cls)
                final_status.append(stat)
                severity.append(round(float(sev), 1))

            part = pd.DataFrame({
                "Timestamp": df[ts],
                "Cluster_ID": cid,
                "Tag": t,
                "Actual_Value": df[t],
                "Tag_Z": final_z[t],
                "Tag_Abs_Z": tag_abs_z,
                "Cluster_PCA_Score": cluster_score,
                "PCA_Loading": float(loadings[t]),
                "Expected_Tag_Z_From_Cluster": expected_z[t],
                "Tag_vs_Cluster_Residual": residual[t],
                "Residual_Z": residual_z[t],
                "Residual_Abs_Z": resid_abs_z,
                "Peer_High_Fraction": peer_abs_high_fraction,
                "Same_Direction_With_Cluster": same_cluster_direction,
                "Peer_Same_Model_Fraction": peer_same_model_fraction,
                "Peer_Opposite_Model_Fraction": peer_opposite_model_fraction,
                "Cluster_High_Any_Fraction": high_any_fraction,
                "Cluster_High_Positive_Fraction": high_pos_fraction,
                "Cluster_High_Negative_Fraction": high_neg_fraction,
                "Cluster_Residual_High_Fraction": residual_high_fraction,
                "Mixed_High_Low_Flag": mixed_flag,
                "Prelim_Outlier_Flag": prelim_outlier,
                "Persistent_Outlier_Flag": persistent,
                "Strong_Single_Point_Flag": strong_single_point,
                "Final_Class": final_class,
                "Final_Status": final_status,
                "Severity_Score_0_100": severity,
                "Top_Peer_Tags": ", ".join(peers[:config["max_peer_tags"]]),
                "Logic_Explanation": "PCA cluster model: actual tag z-score is compared with expected cluster z-score; mismatches are anomalies.",
            })
            all_parts.append(part)

    all_results = pd.concat(all_parts, ignore_index=True)
    # Remove duplicate tag rows if fallback clusters overlap: keep primary cluster if available, else max severity.
    primary_cluster = pd.Series(tag_to_primary, name="Primary_Cluster_ID")
    all_results["Primary_Cluster_ID"] = all_results["Tag"].map(primary_cluster)
    all_results["Is_Primary_Cluster_Row"] = all_results["Cluster_ID"].eq(all_results["Primary_Cluster_ID"])
    all_results = all_results.sort_values(["Is_Primary_Cluster_Row", "Severity_Score_0_100"], ascending=[False, False])
    all_results = all_results.drop_duplicates(subset=["Timestamp", "Tag"], keep="first").sort_values(["Timestamp", "Tag"]).reset_index(drop=True)

    cluster_daily = pd.concat(cluster_daily_parts, ignore_index=True) if cluster_daily_parts else pd.DataFrame()
    loadings_df = pd.DataFrame(loading_rows)
    peer_df = pd.DataFrame(peer_rows).drop_duplicates("Tag") if peer_rows else pd.DataFrame()

    actual = all_results[all_results["Final_Status"].eq("Actual Outlier")].sort_values(["Severity_Score_0_100", "Residual_Abs_Z"], ascending=False)
    warnings = all_results[all_results["Final_Status"].eq("Warning")].sort_values(["Severity_Score_0_100", "Residual_Abs_Z"], ascending=False)
    drift = all_results[all_results["Final_Status"].eq("Cluster Drift")].sort_values(["Timestamp", "Cluster_ID"])

    tag_summary = all_results.groupby("Tag").agg(
        Cluster_ID=("Cluster_ID", "first"),
        Actual_Outlier_Count=("Final_Status", lambda x: int((x == "Actual Outlier").sum())),
        Warning_Count=("Final_Status", lambda x: int((x == "Warning").sum())),
        Cluster_Drift_Count=("Final_Status", lambda x: int((x == "Cluster Drift").sum())),
        Normal_Count=("Final_Status", lambda x: int((x == "Normal").sum())),
        Max_Severity=("Severity_Score_0_100", "max"),
        Avg_Residual_Abs_Z=("Residual_Abs_Z", "mean"),
        Max_Residual_Abs_Z=("Residual_Abs_Z", "max"),
        Top_Peer_Tags=("Top_Peer_Tags", "first"),
    ).reset_index().sort_values(["Actual_Outlier_Count", "Max_Severity"], ascending=False)

    daily_summary = all_results.groupby("Timestamp").agg(
        Actual_Outlier_Count=("Final_Status", lambda x: int((x == "Actual Outlier").sum())),
        Warning_Count=("Final_Status", lambda x: int((x == "Warning").sum())),
        Cluster_Drift_Count=("Final_Status", lambda x: int((x == "Cluster Drift").sum())),
        Normal_Count=("Final_Status", lambda x: int((x == "Normal").sum())),
        Max_Severity=("Severity_Score_0_100", "max"),
    ).reset_index()
    top_tags = actual.groupby("Timestamp")["Tag"].apply(lambda x: ", ".join(x.head(8)))
    daily_summary["Top_Actual_Outlier_Tags"] = daily_summary["Timestamp"].map(top_tags).fillna("")

    outlier_periods = contiguous_periods(all_results.assign(Is_Outlier=all_results["Final_Status"].eq("Actual Outlier")), "Is_Outlier", ["Tag", "Cluster_ID"])

    return {
        "Cluster_Daily_Behavior": cluster_daily,
        "PCA_Loadings": loadings_df,
        "Top_Peer_Tags": peer_df,
        "All_Results": all_results,
        "Actual_Outliers": actual,
        "Warnings": warnings,
        "Cluster_Drift_Supported": drift,
        "Tag_Summary": tag_summary,
        "Daily_Summary": daily_summary,
        "Outlier_Periods": outlier_periods,
    }


def contiguous_periods(df: pd.DataFrame, flag_col: str, group_cols: List[str]) -> pd.DataFrame:
    work = df[df[flag_col].fillna(False)].copy()
    if work.empty:
        return pd.DataFrame(columns=group_cols + ["Start_Timestamp", "End_Timestamp", "Duration_Points", "Max_Severity"])
    work = work.sort_values(group_cols + ["Timestamp"])
    rows = []
    for keys, g in work.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        start = prev = None
        cnt = 0
        max_sev = 0
        for _, row in g.iterrows():
            ts = row["Timestamp"]
            if start is None:
                start = prev = ts
                cnt = 1
                max_sev = row.get("Severity_Score_0_100", 0)
            elif (ts - prev).days <= 1:
                prev = ts
                cnt += 1
                max_sev = max(max_sev, row.get("Severity_Score_0_100", 0))
            else:
                rec = {col: val for col, val in zip(group_cols, keys)}
                rec.update({"Start_Timestamp": start, "End_Timestamp": prev, "Duration_Points": cnt, "Max_Severity": max_sev})
                rows.append(rec)
                start = prev = ts
                cnt = 1
                max_sev = row.get("Severity_Score_0_100", 0)
        if start is not None:
            rec = {col: val for col, val in zip(group_cols, keys)}
            rec.update({"Start_Timestamp": start, "End_Timestamp": prev, "Duration_Points": cnt, "Max_Severity": max_sev})
            rows.append(rec)
    return pd.DataFrame(rows).sort_values(["Max_Severity", "Duration_Points"], ascending=False)


def load_benchmark(path: str, sheet_name: str) -> Optional[pd.DataFrame]:
    if not path or not os.path.exists(path):
        return None
    try:
        b = pd.read_excel(path, sheet_name=sheet_name)
    except Exception:
        b = pd.read_excel(path)
    b = clean_column_names(b)
    ts = find_column(b, ["Timestamp", "Time", "DateTime", "Date"])
    tag = find_column(b, ["Tag", "Target_Tag", "Variable", "Column", "Parameter"])
    status = find_column(b, ["Final_Status", "Final Status", "Status", "Binary_Status"])
    cls = find_column(b, ["Final_Class", "Final Class", "Class"])
    if ts is None or tag is None or (status is None and cls is None):
        return None
    out = pd.DataFrame({"Timestamp": pd.to_datetime(b[ts], errors="coerce"), "Tag": b[tag].astype(str).str.strip()})
    raw = b[status] if status is not None else b[cls]
    out["Benchmark_Raw_Status"] = raw.astype(str)
    out["Benchmark_Binary"] = np.where(out["Benchmark_Raw_Status"].str.lower().str.contains("normal|ok|cluster drift|supported"), "Normal", "Actual Outlier")
    return out.dropna(subset=["Timestamp", "Tag"])


def compare_benchmark(all_results: pd.DataFrame, benchmark: Optional[pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    if benchmark is None:
        return {}
    a = all_results[["Timestamp", "Tag", "Final_Status", "Final_Class", "Severity_Score_0_100"]].copy()
    a["Model_Binary"] = np.where(a["Final_Status"].eq("Actual Outlier"), "Actual Outlier", "Normal")
    comp = a.merge(benchmark, on=["Timestamp", "Tag"], how="inner")
    if comp.empty:
        return {}
    tp = int(((comp["Benchmark_Binary"] == "Actual Outlier") & (comp["Model_Binary"] == "Actual Outlier")).sum())
    tn = int(((comp["Benchmark_Binary"] == "Normal") & (comp["Model_Binary"] == "Normal")).sum())
    fp = int(((comp["Benchmark_Binary"] == "Normal") & (comp["Model_Binary"] == "Actual Outlier")).sum())
    fn = int(((comp["Benchmark_Binary"] == "Actual Outlier") & (comp["Model_Binary"] == "Normal")).sum())
    summary = pd.DataFrame([{
        "Matched_Rows": len(comp), "TP": tp, "TN": tn, "FP": fp, "FN": fn,
        "Accuracy": safe_divide(tp + tn, len(comp)), "Precision": safe_divide(tp, tp + fp),
        "Recall": safe_divide(tp, tp + fn), "Specificity": safe_divide(tn, tn + fp),
        "False_Positive_Rate": safe_divide(fp, fp + tn), "False_Negative_Rate": safe_divide(fn, fn + tp),
    }])
    by_tag = []
    for tag, g in comp.groupby("Tag"):
        tp_t = int(((g["Benchmark_Binary"] == "Actual Outlier") & (g["Model_Binary"] == "Actual Outlier")).sum())
        tn_t = int(((g["Benchmark_Binary"] == "Normal") & (g["Model_Binary"] == "Normal")).sum())
        fp_t = int(((g["Benchmark_Binary"] == "Normal") & (g["Model_Binary"] == "Actual Outlier")).sum())
        fn_t = int(((g["Benchmark_Binary"] == "Actual Outlier") & (g["Model_Binary"] == "Normal")).sum())
        by_tag.append({"Tag": tag, "TP": tp_t, "TN": tn_t, "FP": fp_t, "FN": fn_t, "Precision": safe_divide(tp_t, tp_t + fp_t), "Recall": safe_divide(tp_t, tp_t + fn_t), "Specificity": safe_divide(tn_t, tn_t + fp_t)})
    return {"Benchmark_Summary": summary, "Benchmark_By_Tag": pd.DataFrame(by_tag).sort_values(["FP", "FN"], ascending=False), "Benchmark_Row_Comparison": comp}


def make_dashboard(summary: Dict, config: Dict) -> pd.DataFrame:
    rows = [
        ["Method", "Strict PCA cluster-model consistency without causal matrix"],
        ["Clean Reference Period", f"{summary['clean_start'].date()} to {summary['clean_end'].date()}"],
        ["Total Data Rows", summary["total_rows"]],
        ["Total Tags", summary["total_tags"]],
        ["Total Tag-Timestamp Checks", summary["total_checks"]],
        ["Actual Outlier Rows", summary["actual_outlier_rows"]],
        ["Warning Rows", summary["warning_rows"]],
        ["Cluster Drift Supported Rows", summary["cluster_drift_rows"]],
        ["Normal Rows", summary["normal_rows"]],
        ["Actual Outlier Rate", summary["actual_outlier_rate"]],
        ["Tag Z Limit", config["tag_z_limit"]],
        ["Residual Z Limit", config["residual_z_limit"]],
        ["Main Rule", "High tag z-score + high tag-vs-cluster residual + weak/opposite cluster support"],
        ["Cluster Drift Rule", "Most tags move together under PCA cluster pattern; not counted as isolated outlier"],
    ]
    return pd.DataFrame(rows, columns=["Metric", "Value"])


def write_outputs(output_file: str, full_csv_zip: str, sheets: Dict[str, pd.DataFrame], config: Dict) -> None:
    with zipfile.ZipFile(full_csv_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, df in sheets.items():
            if df is None or df.empty:
                continue
            z.writestr(f"{name[:31]}.csv", df.to_csv(index=False).encode("utf-8"))

    ordered = [
        "Dashboard", "Rules", "Clean_Period_Candidates", "Clean_Detection_Daily", "Tag_Reference_Profile",
        "Learned_Clusters", "Clean_Correlation_Pairs", "PCA_Loadings", "Top_Peer_Tags",
        "Cluster_Daily_Behavior", "Tag_Summary", "Daily_Summary", "Actual_Outliers", "Warnings",
        "Cluster_Drift_Supported", "Outlier_Periods", "All_Results_Sample",
        "Benchmark_Summary", "Benchmark_By_Tag", "Benchmark_Row_Comparison",
    ]
    with pd.ExcelWriter(output_file, engine="xlsxwriter", datetime_format="yyyy-mm-dd") as writer:
        wb = writer.book
        header = wb.add_format({"bold": True, "bg_color": "#1F4E78", "font_color": "white", "border": 1})
        red = wb.add_format({"bg_color": "#F4CCCC"})
        yellow = wb.add_format({"bg_color": "#FFF2CC"})
        green = wb.add_format({"bg_color": "#D9EAD3"})
        date_fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
        pct_fmt = wb.add_format({"num_format": "0.00%"})
        num_fmt = wb.add_format({"num_format": "0.00"})

        for name in ordered:
            df = sheets.get(name)
            if df is None or df.empty:
                continue
            out = df.copy()
            if name == "All_Results_Sample" and len(out) > config["excel_all_results_max_rows"]:
                out = out.head(config["excel_all_results_max_rows"])
            if name in ["Actual_Outliers", "Warnings", "Cluster_Drift_Supported"] and len(out) > config["excel_top_outliers_max_rows"]:
                out = out.head(config["excel_top_outliers_max_rows"])
            out.to_excel(writer, sheet_name=name[:31], index=False)
            ws = writer.sheets[name[:31]]
            for col_idx, col in enumerate(out.columns):
                ws.write(0, col_idx, col, header)
                width = min(max(12, len(str(col)) + 2), 38)
                if col in ["Top_Peer_Tags", "Tags", "Cluster_Tags", "Logic_Explanation"]:
                    width = 45
                ws.set_column(col_idx, col_idx, width)
                lc = str(col).lower()
                if "timestamp" in lc or "date" in lc:
                    ws.set_column(col_idx, col_idx, 14, date_fmt)
                elif "rate" in lc or "fraction" in lc:
                    ws.set_column(col_idx, col_idx, 14, pct_fmt)
                elif "z" in lc or "score" in lc or "count" in lc or "severity" in lc:
                    ws.set_column(col_idx, col_idx, 14, num_fmt)
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, max(len(out), 1), max(len(out.columns) - 1, 0))
            if "Final_Status" in out.columns and len(out) > 0:
                c = out.columns.get_loc("Final_Status")
                ws.conditional_format(1, c, len(out), c, {"type": "text", "criteria": "containing", "value": "Actual Outlier", "format": red})
                ws.conditional_format(1, c, len(out), c, {"type": "text", "criteria": "containing", "value": "Warning", "format": yellow})
                ws.conditional_format(1, c, len(out), c, {"type": "text", "criteria": "containing", "value": "Normal", "format": green})

        if "Daily_Summary" in sheets and not sheets["Daily_Summary"].empty and "Dashboard" in writer.sheets:
            ws = writer.sheets["Dashboard"]
            daily = sheets["Daily_Summary"]
            n = min(len(daily), 300)
            chart = wb.add_chart({"type": "line"})
            chart.add_series({"name": "Actual Outliers", "categories": f"='Daily_Summary'!$A$2:$A${n+1}", "values": f"='Daily_Summary'!$B$2:$B${n+1}"})
            chart.add_series({"name": "Warnings", "categories": f"='Daily_Summary'!$A$2:$A${n+1}", "values": f"='Daily_Summary'!$C$2:$C${n+1}"})
            chart.set_title({"name": "Daily Actual Outliers and Warnings"})
            chart.set_y_axis({"name": "Count"})
            ws.insert_chart("D2", chart, {"x_scale": 1.45, "y_scale": 1.15})


def main(config: Optional[Dict] = None) -> Dict[str, pd.DataFrame]:
    if config is None:
        config = CONFIG.copy()
    df, ts, tag_cols = load_process_data(config)
    clean_daily, clean_candidates, clean_mask = detect_clean_period(df, ts, tag_cols, config)
    ref = build_reference_profile(df, tag_cols, clean_mask, config)
    cluster_df, cluster_map, corr_pairs, corr = build_clusters(df, tag_cols, clean_mask, config)
    results = run_model(df, ts, tag_cols, clean_mask, ref, cluster_df, cluster_map, corr, config)

    total_checks = len(results["All_Results"])
    actual_count = int((results["All_Results"]["Final_Status"] == "Actual Outlier").sum())
    warning_count = int((results["All_Results"]["Final_Status"] == "Warning").sum())
    cluster_drift_count = int((results["All_Results"]["Final_Status"] == "Cluster Drift").sum())
    normal_count = int((results["All_Results"]["Final_Status"] == "Normal").sum())
    selected = clean_candidates.iloc[0]
    summary = {
        "clean_start": selected["Start_Timestamp"],
        "clean_end": selected["End_Timestamp"],
        "total_rows": len(df),
        "total_tags": len(tag_cols),
        "total_checks": total_checks,
        "actual_outlier_rows": actual_count,
        "warning_rows": warning_count,
        "cluster_drift_rows": cluster_drift_count,
        "normal_rows": normal_count,
        "actual_outlier_rate": safe_divide(actual_count, total_checks),
    }
    rules = pd.DataFrame([
        {"Rule": "Clean period", "Value": "Best short stable historical window; avoids relaxed std from long abnormal periods"},
        {"Rule": "Reference scale", "Value": "Uses min(clean std, clean MAD scale * multiplier) to keep limits tight but robust"},
        {"Rule": "Cluster creation", "Value": "Clean-period abs Pearson correlation threshold + fallback peers"},
        {"Rule": "Cluster model", "Value": "PCA first component learns normal up/down pattern inside each cluster"},
        {"Rule": "Actual outlier", "Value": "Tag z high + residual-vs-cluster high + weak/opposite/mixed peer support"},
        {"Rule": "Cluster drift", "Value": "Group moves together under PCA pattern; separated from actual isolated outliers"},
        {"Rule": "tag_z_limit", "Value": config["tag_z_limit"]},
        {"Rule": "residual_z_limit", "Value": config["residual_z_limit"]},
        {"Rule": "persistent_points", "Value": config["persistent_points"]},
        {"Rule": "cluster_residual_high_fraction_max_for_tag_outlier", "Value": config["cluster_residual_high_fraction_max_for_tag_outlier"]},
        {"Rule": "single_point_strong_residual_z", "Value": config["single_point_strong_residual_z"]},
    ])
    sheets = {
        "Dashboard": make_dashboard(summary, config),
        "Rules": rules,
        "Clean_Period_Candidates": clean_candidates,
        "Clean_Detection_Daily": clean_daily,
        "Tag_Reference_Profile": ref,
        "Learned_Clusters": cluster_df,
        "Clean_Correlation_Pairs": corr_pairs,
        **results,
        "All_Results_Sample": results["All_Results"].sort_values(["Final_Status", "Severity_Score_0_100"], ascending=[True, False]),
    }
    bench = load_benchmark(config.get("benchmark_file", ""), config.get("benchmark_sheet_name", "All_Results"))
    sheets.update(compare_benchmark(results["All_Results"], bench))
    write_outputs(config["output_file"], config["full_csv_zip"], sheets, config)
    print("Completed strict PCA cluster outlier detection")
    print(f"Clean period: {summary['clean_start']} to {summary['clean_end']}")
    print(f"Actual outliers: {actual_count}/{total_checks} ({summary['actual_outlier_rate']:.2%})")
    print(f"Warnings: {warning_count}, Cluster drift supported: {cluster_drift_count}, Normal: {normal_count}")
    print(f"Excel: {config['output_file']}")
    print(f"Full CSV ZIP: {config['full_csv_zip']}")
    return sheets


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Strict PCA cluster consistency outlier detection without causal matrix")
    p.add_argument("--data_file", default=CONFIG["data_file"])
    p.add_argument("--data_sheet_name", default=CONFIG["data_sheet_name"])
    p.add_argument("--timestamp_col", default=CONFIG["timestamp_col"])
    p.add_argument("--output_file", default=CONFIG["output_file"])
    p.add_argument("--full_csv_zip", default=CONFIG["full_csv_zip"])
    p.add_argument("--benchmark_file", default="")
    p.add_argument("--benchmark_sheet_name", default="All_Results")
    p.add_argument("--tag_z_limit", type=float, default=CONFIG["tag_z_limit"])
    p.add_argument("--residual_z_limit", type=float, default=CONFIG["residual_z_limit"])
    p.add_argument("--warning_tag_z_limit", type=float, default=CONFIG["warning_tag_z_limit"])
    p.add_argument("--warning_residual_z_limit", type=float, default=CONFIG["warning_residual_z_limit"])
    p.add_argument("--cluster_abs_corr_threshold", type=float, default=CONFIG["cluster_abs_corr_threshold"])
    p.add_argument("--cluster_support_fraction_min", type=float, default=CONFIG["cluster_support_fraction_min"])
    p.add_argument("--isolated_peer_high_fraction_max", type=float, default=CONFIG["isolated_peer_high_fraction_max"])
    p.add_argument("--persistent_points", type=int, default=CONFIG["persistent_points"])
    p.add_argument("--cluster_residual_high_fraction_max_for_tag_outlier", type=float, default=CONFIG["cluster_residual_high_fraction_max_for_tag_outlier"])
    p.add_argument("--clean_window_points", type=int, default=CONFIG["clean_window_points"])
    p.add_argument("--clean_window_min_points", type=int, default=CONFIG["clean_window_min_points"])
    p.add_argument("--mad_scale_multiplier", type=float, default=CONFIG["mad_scale_multiplier"])
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    cfg = CONFIG.copy()
    cfg.update(vars(args))
    main(cfg)
