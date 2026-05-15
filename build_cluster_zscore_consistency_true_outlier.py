"""
WITHOUT CAUSAL MATRIX - CLUSTER Z-SCORE CONSISTENCY OUTLIER DETECTION V2

Goal
----
Detect only meaningful / actual outliers by comparing each tag against the
behaviour of its learned peer cluster.

Core idea
---------
1) Detect a clean/reference period from historical data.
2) Build tag clusters from clean-period correlations only.
3) Calculate tag z-scores using clean-period mean/std and median/MAD.
4) Use correlation-sign-adjusted peer z-scores, so negatively-correlated peers are treated correctly.
5) For each timestamp and each cluster:
      - If all/most tags in a cluster move high/low together, treat it as
        "Cluster Drift - Supported" and do NOT count it as an isolated outlier.
      - If one/few tags show high deviation while the peer cluster does not,
        flag "Actual Outlier - Isolated".
      - If the cluster is moving one way but the tag moves opposite / differently,
        flag "Actual Outlier - Cluster Mismatch".
      - If both high and low deviations exist inside the same cluster, mark
        mixed cluster behaviour and tag-level risk.

This script is intentionally WITHOUT causal matrix. The drivers/cluster tags are
statistical peers, not confirmed process causes.

Run example
-----------
python build_cluster_zscore_consistency_outlier_v2.py \
  --data_file "Multi_X_Multi_Y_Correct_Data.xlsx" \
  --output_file "cluster_zscore_consistency_outlier_v2.xlsx"

Optional benchmark comparison:
python build_cluster_zscore_consistency_outlier_v2.py \
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
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


CONFIG: Dict = {
    # Input / output
    "data_file": "Multi_X_Multi_Y_Correct_Data.xlsx",
    "data_sheet_name": None,
    "timestamp_col": "Timestamp",
    "output_file": "cluster_zscore_consistency_true_outlier.xlsx",
    "full_csv_zip": "cluster_zscore_consistency_true_outlier_full_csv.zip",

    # Clean period detection
    "min_clean_period_points": 360,
    "clean_candidate_bad_fraction_limit": 0.12,
    "clean_candidate_score_quantile": 0.45,
    "global_candidate_z_limit": 2.8,
    "max_clean_bad_fraction_for_window": 0.16,

    # Clustering
    "cluster_abs_corr_threshold": 0.55,
    "cluster_min_size": 3,
    "max_peer_tags": 8,

    # Deviation thresholds
    # std_multiplier is the main user-tunable threshold.
    "std_multiplier": 3.5,
    "strong_std_multiplier": 4.8,
    "cluster_residual_std_multiplier": 3.5,
    "strong_cluster_residual_std_multiplier": 4.5,
    "warning_std_multiplier": 2.8,
    "warning_cluster_residual_std_multiplier": 2.8,

    # Cluster consistency rules
    "peer_same_direction_support_min": 0.50,
    "peer_opposite_direction_limit": 0.35,
    "peer_high_fraction_supported_min": 0.50,
    "peer_high_fraction_isolated_max": 0.20,
    "mixed_high_low_fraction_min": 0.25,
    "minor_direction_z": 1.0,
    "cluster_group_high_z": 2.0,

    # Output limits for Excel. Full row-level data is also written to CSV ZIP.
    "excel_all_results_max_rows": 20000,
    "excel_top_outliers_max_rows": 5000,
}


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

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
        scale = float(x.std(ddof=1))
    if pd.isna(scale) or scale < eps:
        scale = eps
    return med, scale


def robust_z(s: pd.Series, center: Optional[float] = None, scale: Optional[float] = None, eps: float = 1e-9) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    if center is None or scale is None:
        center, scale = robust_center_scale(x, eps)
    if pd.isna(scale) or scale < eps:
        scale = eps
    return (x - center) / scale


def contiguous_periods(df: pd.DataFrame, flag_col: str, group_cols: List[str], timestamp_col: str = "Timestamp") -> pd.DataFrame:
    """Build contiguous periods for rows where flag_col is True."""
    if df.empty or flag_col not in df.columns:
        return pd.DataFrame()
    work = df[df[flag_col].fillna(False)].copy()
    if work.empty:
        return pd.DataFrame()
    work[timestamp_col] = pd.to_datetime(work[timestamp_col], errors="coerce")
    work = work.dropna(subset=[timestamp_col]).sort_values(group_cols + [timestamp_col])
    out = []
    for keys, g in work.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        g = g.sort_values(timestamp_col)
        start = prev = None
        count = 0
        max_sev = 0.0
        first_class = None
        for _, row in g.iterrows():
            ts = row[timestamp_col]
            sev = float(row.get("Severity_Score_0_100", 0) or 0)
            cls = row.get("Final_Class", "")
            if start is None:
                start = prev = ts
                count = 1
                max_sev = sev
                first_class = cls
            elif (ts - prev).days <= 1:
                prev = ts
                count += 1
                max_sev = max(max_sev, sev)
            else:
                rec = {col: val for col, val in zip(group_cols, keys)}
                rec.update({"Start_Timestamp": start, "End_Timestamp": prev, "Duration_Points": count, "Max_Severity": max_sev, "Period_Class": first_class})
                out.append(rec)
                start = prev = ts
                count = 1
                max_sev = sev
                first_class = cls
        if start is not None:
            rec = {col: val for col, val in zip(group_cols, keys)}
            rec.update({"Start_Timestamp": start, "End_Timestamp": prev, "Duration_Points": count, "Max_Severity": max_sev, "Period_Class": first_class})
            out.append(rec)
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# Loading process data
# ---------------------------------------------------------------------------

def read_excel_file(path: str, sheet_name=None) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if sheet_name is None or str(sheet_name).strip() == "":
        return pd.read_excel(path)
    return pd.read_excel(path, sheet_name=sheet_name)


def load_process_data(config: Dict) -> Tuple[pd.DataFrame, str, List[str]]:
    df = clean_column_names(read_excel_file(config["data_file"], config.get("data_sheet_name")))
    ts = find_column(df, [config.get("timestamp_col", "Timestamp"), "Timestamp", "Time", "DateTime", "Date"])
    if ts is None:
        raise ValueError("Timestamp column not found. Please provide --timestamp_col.")
    df[ts] = pd.to_datetime(df[ts], errors="coerce")
    df = df.dropna(subset=[ts]).sort_values(ts).reset_index(drop=True)

    tag_cols = []
    for col in df.columns:
        if col == ts:
            continue
        x = pd.to_numeric(df[col], errors="coerce")
        if x.notna().sum() >= 10:
            df[col] = x
            tag_cols.append(col)
    if len(tag_cols) < 2:
        raise ValueError("At least two numeric tags are required.")
    return df, ts, tag_cols


# ---------------------------------------------------------------------------
# Step 1: Clean/reference period detection
# ---------------------------------------------------------------------------

def detect_clean_period(df: pd.DataFrame, ts: str, tag_cols: List[str], config: Dict) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Find longest stable clean/reference period from historical data."""
    z = pd.DataFrame(index=df.index)
    for c in tag_cols:
        z[c] = robust_z(df[c])

    abs_z = z.abs()
    bad_fraction = (abs_z > config["global_candidate_z_limit"]).mean(axis=1)
    row_median_abs_z = abs_z.median(axis=1)
    row_p90_abs_z = abs_z.quantile(0.90, axis=1)
    row_score = row_median_abs_z + 0.60 * row_p90_abs_z + 2.50 * bad_fraction
    score_cut = row_score.quantile(config["clean_candidate_score_quantile"])

    is_clean_candidate = (
        (bad_fraction <= config["clean_candidate_bad_fraction_limit"]) &
        (row_score <= score_cut)
    )

    clean_daily = pd.DataFrame({
        "Timestamp": df[ts],
        "Bad_Tag_Fraction": bad_fraction,
        "Median_Abs_Global_Z": row_median_abs_z,
        "P90_Abs_Global_Z": row_p90_abs_z,
        "Clean_Score": row_score,
        "Clean_Score_Cutoff": score_cut,
        "Is_Clean_Candidate": is_clean_candidate,
    })

    # Build all candidate contiguous periods and score them.
    periods = []
    start_idx = None
    prev_idx = None
    for idx, flag in enumerate(is_clean_candidate.tolist()):
        if flag and start_idx is None:
            start_idx = idx
            prev_idx = idx
        elif flag:
            prev_idx = idx
        elif start_idx is not None:
            periods.append((start_idx, prev_idx))
            start_idx = prev_idx = None
    if start_idx is not None:
        periods.append((start_idx, prev_idx))

    cand_rows = []
    for s, e in periods:
        g = clean_daily.iloc[s:e+1]
        if len(g) < config["min_clean_period_points"]:
            continue
        cand_rows.append({
            "Start_Timestamp": df.loc[s, ts],
            "End_Timestamp": df.loc[e, ts],
            "Start_Row_Index": s,
            "End_Row_Index": e,
            "Duration_Points": e - s + 1,
            "Avg_Bad_Tag_Fraction": g["Bad_Tag_Fraction"].mean(),
            "Max_Bad_Tag_Fraction": g["Bad_Tag_Fraction"].max(),
            "Avg_Clean_Score": g["Clean_Score"].mean(),
            "Median_Clean_Score": g["Clean_Score"].median(),
        })

    candidates = pd.DataFrame(cand_rows)
    if candidates.empty:
        # fallback: choose lowest-scoring rolling-like block without moving average dependency
        n = int(min(max(config["min_clean_period_points"], 60), len(df)))
        best = None
        scores = row_score.values
        bad = bad_fraction.values
        for s in range(0, len(df) - n + 1):
            e = s + n - 1
            avg_bad = float(np.nanmean(bad[s:e+1]))
            avg_score = float(np.nanmean(scores[s:e+1]))
            max_bad = float(np.nanmax(bad[s:e+1]))
            if avg_bad <= config["max_clean_bad_fraction_for_window"]:
                metric = avg_score + avg_bad * 5
                if best is None or metric < best[0]:
                    best = (metric, s, e, avg_bad, avg_score, max_bad)
        if best is None:
            # Last fallback: best low-score block regardless of bad fraction.
            for s in range(0, len(df) - n + 1):
                e = s + n - 1
                avg_bad = float(np.nanmean(bad[s:e+1]))
                avg_score = float(np.nanmean(scores[s:e+1]))
                metric = avg_score + avg_bad * 5
                if best is None or metric < best[0]:
                    best = (metric, s, e, avg_bad, avg_score, float(np.nanmax(bad[s:e+1])))
        _, s, e, avg_bad, avg_score, max_bad = best
        candidates = pd.DataFrame([{
            "Start_Timestamp": df.loc[s, ts],
            "End_Timestamp": df.loc[e, ts],
            "Start_Row_Index": s,
            "End_Row_Index": e,
            "Duration_Points": e - s + 1,
            "Avg_Bad_Tag_Fraction": avg_bad,
            "Max_Bad_Tag_Fraction": max_bad,
            "Avg_Clean_Score": avg_score,
            "Median_Clean_Score": np.nanmedian(scores[s:e+1]),
            "Fallback_Selected": True,
        }])

    candidates["Clean_Rank_Score"] = (
        candidates["Avg_Clean_Score"] +
        3.0 * candidates["Avg_Bad_Tag_Fraction"] -
        0.002 * candidates["Duration_Points"]
    )
    candidates = candidates.sort_values(["Clean_Rank_Score", "Avg_Bad_Tag_Fraction", "Duration_Points"], ascending=[True, True, False]).reset_index(drop=True)
    selected = candidates.iloc[0]
    clean_mask = (df[ts] >= selected["Start_Timestamp"]) & (df[ts] <= selected["End_Timestamp"])
    clean_daily["Selected_Clean_Period"] = clean_mask.values
    return clean_daily, candidates, clean_mask


# ---------------------------------------------------------------------------
# Step 2: Reference profiles and clusters from clean data
# ---------------------------------------------------------------------------

def build_reference_profile(df: pd.DataFrame, tag_cols: List[str], clean_mask: pd.Series) -> pd.DataFrame:
    rows = []
    for tag in tag_cols:
        x = pd.to_numeric(df.loc[clean_mask, tag], errors="coerce").dropna()
        med, mad_scale = robust_center_scale(x)
        mean = float(x.mean()) if len(x) else np.nan
        std = float(x.std(ddof=1)) if len(x) > 1 else np.nan
        if pd.isna(std) or std <= 1e-9:
            std = mad_scale
        rows.append({
            "Tag": tag,
            "Clean_Count": int(len(x)),
            "Clean_Mean": mean,
            "Clean_Std": std,
            "Clean_Median": med,
            "Clean_MAD_Scale": mad_scale,
            "Clean_Min": float(x.min()) if len(x) else np.nan,
            "Clean_Max": float(x.max()) if len(x) else np.nan,
            "Clean_P01": float(x.quantile(0.01)) if len(x) else np.nan,
            "Clean_P05": float(x.quantile(0.05)) if len(x) else np.nan,
            "Clean_P95": float(x.quantile(0.95)) if len(x) else np.nan,
            "Clean_P99": float(x.quantile(0.99)) if len(x) else np.nan,
        })
    return pd.DataFrame(rows)


def build_clean_z_matrices(df: pd.DataFrame, tag_cols: List[str], ref: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ref_idx = ref.set_index("Tag")
    std_z = pd.DataFrame(index=df.index)
    robust_z_df = pd.DataFrame(index=df.index)
    effective_abs_z = pd.DataFrame(index=df.index)
    for tag in tag_cols:
        x = pd.to_numeric(df[tag], errors="coerce")
        mean = ref_idx.loc[tag, "Clean_Mean"]
        std = ref_idx.loc[tag, "Clean_Std"]
        med = ref_idx.loc[tag, "Clean_Median"]
        mad_scale = ref_idx.loc[tag, "Clean_MAD_Scale"]
        if pd.isna(std) or std <= 1e-9:
            std = 1e-9
        if pd.isna(mad_scale) or mad_scale <= 1e-9:
            mad_scale = std
        sz = (x - mean) / std
        rz = (x - med) / mad_scale
        std_z[tag] = sz
        robust_z_df[tag] = rz
        # Conservative z: both std and robust scale must agree before calling it extreme.
        effective_abs_z[tag] = np.minimum(sz.abs(), rz.abs())
    return std_z, robust_z_df, effective_abs_z


def _connected_components(nodes: List[str], edges: Dict[str, List[str]]) -> List[List[str]]:
    seen = set()
    comps = []
    for n in nodes:
        if n in seen:
            continue
        stack = [n]
        seen.add(n)
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


def build_clusters_from_clean(df: pd.DataFrame, tag_cols: List[str], clean_mask: pd.Series, config: Dict) -> Tuple[pd.DataFrame, Dict[str, List[str]], pd.DataFrame]:
    clean_df = df.loc[clean_mask, tag_cols].apply(pd.to_numeric, errors="coerce")
    corr = clean_df.corr(method="pearson").fillna(0.0)
    threshold = config["cluster_abs_corr_threshold"]
    edges = {tag: [] for tag in tag_cols}
    pairs = []
    for i, a in enumerate(tag_cols):
        for b in tag_cols[i+1:]:
            c = float(corr.loc[a, b])
            if abs(c) >= threshold:
                edges[a].append(b)
                edges[b].append(a)
                pairs.append({"Tag_A": a, "Tag_B": b, "Clean_Correlation": c, "Abs_Correlation": abs(c)})

    comps = _connected_components(tag_cols, edges)

    # Ensure singleton/small components are attached to best correlated peers if possible.
    clusters = []
    assigned = set()
    cluster_id = 1
    for comp in sorted(comps, key=lambda x: (-len(x), x[0])):
        if len(comp) >= config["cluster_min_size"]:
            clusters.append((f"Cluster_{cluster_id:02d}", sorted(comp)))
            assigned.update(comp)
            cluster_id += 1

    for tag in tag_cols:
        if tag in assigned:
            continue
        # Create a small peer group from top correlations so every tag has a comparison cluster.
        top = corr[tag].drop(index=tag, errors="ignore").abs().sort_values(ascending=False).head(config["cluster_min_size"] - 1).index.tolist()
        group = sorted(list(dict.fromkeys([tag] + top)))
        clusters.append((f"Cluster_{cluster_id:02d}", group))
        assigned.update([tag])
        cluster_id += 1

    tag_to_cluster = {}
    cluster_map = {}
    rows = []
    for cid, tags in clusters:
        cluster_map[cid] = tags
        for tag in tags:
            # A tag can appear as peer in fallback cluster, but primary assignment should be first exact owner.
            if tag not in tag_to_cluster:
                tag_to_cluster[tag] = cid
        # Cluster average absolute corr
        subcorr = corr.loc[tags, tags].where(~np.eye(len(tags), dtype=bool))
        avg_abs_corr = float(subcorr.abs().stack().mean()) if len(tags) > 1 else np.nan
        rows.append({
            "Cluster_ID": cid,
            "Cluster_Size": len(tags),
            "Avg_Abs_Clean_Correlation": avg_abs_corr,
            "Tags": ", ".join(tags),
        })

    cluster_df = pd.DataFrame(rows)
    pair_df = pd.DataFrame(pairs).sort_values("Abs_Correlation", ascending=False) if pairs else pd.DataFrame(columns=["Tag_A", "Tag_B", "Clean_Correlation", "Abs_Correlation"])
    return cluster_df, cluster_map, pair_df


def build_top_peers(corr: pd.DataFrame, tag_cols: List[str], cluster_map: Dict[str, List[str]], tag_to_cluster: Dict[str, str], max_peers: int) -> Dict[str, List[str]]:
    peers = {}
    for tag in tag_cols:
        cid = tag_to_cluster[tag]
        cluster_tags = [t for t in cluster_map.get(cid, []) if t != tag]
        if len(cluster_tags) >= 1:
            ordered = corr.loc[tag, cluster_tags].abs().sort_values(ascending=False).head(max_peers).index.tolist()
        else:
            ordered = corr[tag].drop(index=tag, errors="ignore").abs().sort_values(ascending=False).head(max_peers).index.tolist()
        peers[tag] = ordered
    return peers


# ---------------------------------------------------------------------------
# Step 3: Cluster consistency scoring and classification
# ---------------------------------------------------------------------------

def prepare_cluster_residual_reference(std_z: pd.DataFrame, clean_mask: pd.Series, tag_cols: List[str], top_peers: Dict[str, List[str]]) -> pd.DataFrame:
    rows = []
    for tag in tag_cols:
        peers = top_peers.get(tag, [])
        if peers:
            peer_med = std_z.loc[clean_mask, peers].median(axis=1)
            resid = std_z.loc[clean_mask, tag] - peer_med
        else:
            resid = std_z.loc[clean_mask, tag]
        center = float(resid.median()) if len(resid.dropna()) else 0.0
        std = float(resid.std(ddof=1)) if len(resid.dropna()) > 1 else np.nan
        med, mad_scale = robust_center_scale(resid)
        # Conservative residual scale: use the larger scale to avoid false positives.
        scale = np.nanmax([std, mad_scale, 1e-9])
        rows.append({
            "Tag": tag,
            "Clean_Residual_Center": center,
            "Clean_Residual_Std": std,
            "Clean_Residual_Median": med,
            "Clean_Residual_MAD_Scale": mad_scale,
            "Final_Residual_Scale_Used": scale,
        })
    return pd.DataFrame(rows)


def classify_result(tag_abs_z, tag_z, residual_abs_z, same_support, opposite_fraction, peer_high_fraction,
                    peer_low_fraction, cluster_high_fraction, cluster_low_fraction, config: Dict) -> Tuple[str, str, int]:
    """Return final_class, final_status, severity score."""
    std_lim = config["std_multiplier"]
    strong_lim = config["strong_std_multiplier"]
    res_lim = config["cluster_residual_std_multiplier"]
    strong_res_lim = config["strong_cluster_residual_std_multiplier"]
    warn_z = config["warning_std_multiplier"]
    warn_res = config["warning_cluster_residual_std_multiplier"]

    strong_individual = (tag_abs_z >= strong_lim) and (residual_abs_z >= strong_res_lim)
    high_individual = (tag_abs_z >= std_lim) and (residual_abs_z >= res_lim)
    warning_individual = (tag_abs_z >= warn_z) and (residual_abs_z >= warn_res)

    supported_cluster_move = (
        tag_abs_z >= std_lim and
        peer_high_fraction >= config["peer_high_fraction_supported_min"] and
        same_support >= config["peer_same_direction_support_min"] and
        opposite_fraction < config["peer_opposite_direction_limit"]
    )

    isolated_tag_move = (
        tag_abs_z >= std_lim and
        peer_high_fraction <= config["peer_high_fraction_isolated_max"] and
        residual_abs_z >= res_lim
    )

    opposite_cluster_move = (
        tag_abs_z >= std_lim and
        opposite_fraction >= config["peer_opposite_direction_limit"] and
        residual_abs_z >= res_lim
    )

    mixed_cluster = (
        cluster_high_fraction >= config["mixed_high_low_fraction_min"] and
        cluster_low_fraction >= config["mixed_high_low_fraction_min"]
    )

    if strong_individual and (isolated_tag_move or opposite_cluster_move):
        cls = "Actual Outlier - Strong Cluster Mismatch"
        status = "Actual Outlier"
    elif high_individual and isolated_tag_move:
        cls = "Actual Outlier - Isolated Tag"
        status = "Actual Outlier"
    elif high_individual and opposite_cluster_move:
        cls = "Actual Outlier - Opposite to Cluster"
        status = "Actual Outlier"
    elif supported_cluster_move:
        cls = "Cluster Drift - Supported"
        status = "Cluster Drift"
    elif warning_individual and mixed_cluster:
        cls = "Warning - Mixed Cluster Behaviour"
        status = "Warning"
    elif warning_individual:
        cls = "Warning - Check Cluster Consistency"
        status = "Warning"
    else:
        cls = "Normal"
        status = "Normal"

    # Severity emphasizes true mismatch more than raw deviation.
    severity = 0.0
    severity += min(35, max(0, tag_abs_z) * 6)
    severity += min(45, max(0, residual_abs_z) * 9)
    severity += 15 * max(0, opposite_fraction)
    severity += 10 * max(0, 1 - same_support) if tag_abs_z >= warn_z else 0
    if status == "Cluster Drift":
        severity *= 0.55
    if status == "Normal":
        severity = min(severity, 25)
    return cls, status, int(round(min(100, severity)))


def run_cluster_consistency_model(df: pd.DataFrame, ts: str, tag_cols: List[str], clean_mask: pd.Series,
                                  ref: pd.DataFrame, cluster_df: pd.DataFrame, cluster_map: Dict[str, List[str]],
                                  config: Dict) -> Dict[str, pd.DataFrame]:
    std_z, robust_z_df, effective_abs_z = build_clean_z_matrices(df, tag_cols, ref)
    clean_df = df.loc[clean_mask, tag_cols].apply(pd.to_numeric, errors="coerce")
    corr = clean_df.corr(method="pearson").fillna(0.0)

    tag_to_cluster = {}
    for _, row in cluster_df.iterrows():
        cid = row["Cluster_ID"]
        tags = [t.strip() for t in str(row["Tags"]).split(",")]
        for tag in tags:
            if tag in tag_cols and tag not in tag_to_cluster:
                tag_to_cluster[tag] = cid
    for tag in tag_cols:
        if tag not in tag_to_cluster:
            tag_to_cluster[tag] = cluster_df.iloc[0]["Cluster_ID"]

    top_peers = build_top_peers(corr, tag_cols, cluster_map, tag_to_cluster, config["max_peer_tags"])
    resid_ref = prepare_cluster_residual_reference(std_z, clean_mask, tag_cols, top_peers)
    resid_ref_idx = resid_ref.set_index("Tag")

    all_rows = []
    cluster_rows = []

    # Cluster daily behaviour first.
    for cid, tags in cluster_map.items():
        tags = [t for t in tags if t in tag_cols]
        if not tags:
            continue
        z_sub = std_z[tags]
        abs_sub = effective_abs_z[tags]
        high_pos = (z_sub >= config["cluster_group_high_z"]).mean(axis=1)
        high_neg = (z_sub <= -config["cluster_group_high_z"]).mean(axis=1)
        high_any = (abs_sub >= config["cluster_group_high_z"]).mean(axis=1)
        group_median_z = z_sub.median(axis=1)
        group_abs_median_z = abs_sub.median(axis=1)
        mixed_flag = (high_pos >= config["mixed_high_low_fraction_min"]) & (high_neg >= config["mixed_high_low_fraction_min"])
        cluster_drift_flag = (high_any >= config["peer_high_fraction_supported_min"]) & (~mixed_flag)
        cluster_rows.append(pd.DataFrame({
            "Timestamp": df[ts],
            "Cluster_ID": cid,
            "Cluster_Size": len(tags),
            "Group_Median_Std_Z": group_median_z,
            "Group_Median_Abs_Effective_Z": group_abs_median_z,
            "Cluster_High_Positive_Fraction": high_pos,
            "Cluster_High_Negative_Fraction": high_neg,
            "Cluster_High_Any_Fraction": high_any,
            "Mixed_High_Low_Flag": mixed_flag,
            "Cluster_Drift_Supported_Flag": cluster_drift_flag,
            "Cluster_Tags": ", ".join(tags),
        }))

    cluster_daily = pd.concat(cluster_rows, ignore_index=True) if cluster_rows else pd.DataFrame()

    for tag in tag_cols:
        cid = tag_to_cluster[tag]
        peers = top_peers.get(tag, [])
        peer_z = std_z[peers] if peers else pd.DataFrame(index=df.index)
        peer_effective_abs = effective_abs_z[peers] if peers else pd.DataFrame(index=df.index)

        tag_z = std_z[tag]
        tag_robust_z = robust_z_df[tag]
        tag_abs_z = effective_abs_z[tag]
        direction = np.sign(tag_z).replace(0, np.nan)

        if peers:
            # IMPORTANT: correlation-sign adjustment.
            # If target and peer are negatively correlated, peer LOW supports target HIGH.
            # So we multiply each peer z-score by sign(corr(target, peer)) before comparing direction.
            sign_map = {p: (1.0 if corr.loc[tag, p] >= 0 else -1.0) for p in peers}
            adjusted_peer_z = peer_z.copy()
            for p in peers:
                adjusted_peer_z[p] = adjusted_peer_z[p] * sign_map[p]

            peer_med_z = adjusted_peer_z.median(axis=1)
            peer_high_fraction = (adjusted_peer_z.abs() >= config["cluster_group_high_z"]).mean(axis=1)
            peer_same_direction = ((np.sign(adjusted_peer_z).eq(np.sign(tag_z), axis=0)) & (adjusted_peer_z.abs() >= config["minor_direction_z"])).mean(axis=1)
            peer_opposite_direction = ((np.sign(adjusted_peer_z).eq(-np.sign(tag_z), axis=0)) & (adjusted_peer_z.abs() >= config["minor_direction_z"])).mean(axis=1)
            peer_low_fraction = (adjusted_peer_z <= -config["cluster_group_high_z"]).mean(axis=1)
            peer_high_pos_fraction = (adjusted_peer_z >= config["cluster_group_high_z"]).mean(axis=1)
        else:
            peer_med_z = pd.Series(0.0, index=df.index)
            peer_high_fraction = pd.Series(0.0, index=df.index)
            peer_same_direction = pd.Series(0.0, index=df.index)
            peer_opposite_direction = pd.Series(0.0, index=df.index)
            peer_low_fraction = pd.Series(0.0, index=df.index)
            peer_high_pos_fraction = pd.Series(0.0, index=df.index)

        residual = tag_z - peer_med_z
        res_center = resid_ref_idx.loc[tag, "Clean_Residual_Center"]
        res_scale = resid_ref_idx.loc[tag, "Final_Residual_Scale_Used"]
        if pd.isna(res_scale) or res_scale <= 1e-9:
            res_scale = 1.0
        residual_z = (residual - res_center) / res_scale

        # Map cluster-wide stats for this tag.
        cd = cluster_daily[cluster_daily["Cluster_ID"] == cid].set_index("Timestamp") if not cluster_daily.empty else pd.DataFrame()
        if not cd.empty:
            cluster_high_any = df[ts].map(cd["Cluster_High_Any_Fraction"])
            cluster_high_pos = df[ts].map(cd["Cluster_High_Positive_Fraction"])
            cluster_high_neg = df[ts].map(cd["Cluster_High_Negative_Fraction"])
            mixed_flag = df[ts].map(cd["Mixed_High_Low_Flag"])
        else:
            cluster_high_any = pd.Series(0.0, index=df.index)
            cluster_high_pos = pd.Series(0.0, index=df.index)
            cluster_high_neg = pd.Series(0.0, index=df.index)
            mixed_flag = pd.Series(False, index=df.index)

        classes = []
        statuses = []
        severities = []
        for i in range(len(df)):
            cls, status, sev = classify_result(
                tag_abs_z=float(tag_abs_z.iloc[i]) if pd.notna(tag_abs_z.iloc[i]) else 0.0,
                tag_z=float(tag_z.iloc[i]) if pd.notna(tag_z.iloc[i]) else 0.0,
                residual_abs_z=float(abs(residual_z.iloc[i])) if pd.notna(residual_z.iloc[i]) else 0.0,
                same_support=float(peer_same_direction.iloc[i]) if pd.notna(peer_same_direction.iloc[i]) else 0.0,
                opposite_fraction=float(peer_opposite_direction.iloc[i]) if pd.notna(peer_opposite_direction.iloc[i]) else 0.0,
                peer_high_fraction=float(peer_high_fraction.iloc[i]) if pd.notna(peer_high_fraction.iloc[i]) else 0.0,
                peer_low_fraction=float(peer_low_fraction.iloc[i]) if pd.notna(peer_low_fraction.iloc[i]) else 0.0,
                cluster_high_fraction=float(peer_high_pos_fraction.iloc[i]) if pd.notna(peer_high_pos_fraction.iloc[i]) else 0.0,
                cluster_low_fraction=float(peer_low_fraction.iloc[i]) if pd.notna(peer_low_fraction.iloc[i]) else 0.0,
                config=config,
            )
            classes.append(cls)
            statuses.append(status)
            severities.append(sev)

        explanation = np.select(
            [
                np.array(statuses) == "Actual Outlier",
                np.array(statuses) == "Cluster Drift",
                np.array(statuses) == "Warning",
            ],
            [
                "Tag deviation is not supported by its learned peer cluster; possible true anomaly/drift.",
                "Tag deviation is supported by most peer tags in same direction; likely common process shift, not isolated outlier.",
                "Borderline mismatch or mixed cluster behaviour; review before treating as true outlier.",
            ],
            default="Tag behaviour is consistent with clean-period cluster behaviour."
        )

        out = pd.DataFrame({
            "Timestamp": df[ts],
            "Tag": tag,
            "Cluster_ID": cid,
            "Actual_Value": df[tag],
            "Clean_Mean": ref.set_index("Tag").loc[tag, "Clean_Mean"],
            "Clean_Std": ref.set_index("Tag").loc[tag, "Clean_Std"],
            "Tag_Std_Z": tag_z,
            "Tag_Robust_Z": tag_robust_z,
            "Effective_Abs_Z_Conservative": tag_abs_z,
            "Peer_Median_Std_Z": peer_med_z,
            "Tag_vs_Peer_Residual_Z": residual_z,
            "Peer_High_Fraction": peer_high_fraction,
            "Peer_Same_Direction_Support": peer_same_direction,
            "Peer_Opposite_Direction_Fraction": peer_opposite_direction,
            "Cluster_High_Any_Fraction": cluster_high_any,
            "Cluster_High_Positive_Fraction": cluster_high_pos,
            "Cluster_High_Negative_Fraction": cluster_high_neg,
            "Mixed_Cluster_Behaviour": mixed_flag,
            "Top_Peer_Tags": ", ".join(peers),
            "Final_Class": classes,
            "Final_Status": statuses,
            "Severity_Score_0_100": severities,
            "Explanation": explanation,
        })
        all_rows.append(out)

    all_results = pd.concat(all_rows, ignore_index=True)

    actual_outliers = all_results[all_results["Final_Status"] == "Actual Outlier"].sort_values(
        ["Severity_Score_0_100", "Timestamp"], ascending=[False, True]
    )
    warnings = all_results[all_results["Final_Status"] == "Warning"].sort_values(
        ["Severity_Score_0_100", "Timestamp"], ascending=[False, True]
    )
    cluster_drift = all_results[all_results["Final_Status"] == "Cluster Drift"].sort_values(
        ["Timestamp", "Cluster_ID", "Severity_Score_0_100"], ascending=[True, True, False]
    )

    tag_summary = all_results.groupby("Tag").agg(
        Cluster_ID=("Cluster_ID", "first"),
        Total_Points=("Timestamp", "count"),
        Actual_Outlier_Count=("Final_Status", lambda x: (x == "Actual Outlier").sum()),
        Warning_Count=("Final_Status", lambda x: (x == "Warning").sum()),
        Cluster_Drift_Count=("Final_Status", lambda x: (x == "Cluster Drift").sum()),
        Normal_Count=("Final_Status", lambda x: (x == "Normal").sum()),
        Max_Severity=("Severity_Score_0_100", "max"),
        Avg_Abs_Tag_Z=("Effective_Abs_Z_Conservative", lambda x: np.nanmean(np.abs(x))),
        Avg_Abs_Cluster_Residual_Z=("Tag_vs_Peer_Residual_Z", lambda x: np.nanmean(np.abs(x))),
        Top_Peer_Tags=("Top_Peer_Tags", "first"),
    ).reset_index()
    tag_summary["Actual_Outlier_Rate"] = tag_summary["Actual_Outlier_Count"] / tag_summary["Total_Points"]
    tag_summary = tag_summary.sort_values(["Actual_Outlier_Count", "Max_Severity"], ascending=[False, False])

    daily_summary = all_results.groupby("Timestamp").agg(
        Total_Tag_Checks=("Tag", "count"),
        Actual_Outlier_Count=("Final_Status", lambda x: (x == "Actual Outlier").sum()),
        Warning_Count=("Final_Status", lambda x: (x == "Warning").sum()),
        Cluster_Drift_Count=("Final_Status", lambda x: (x == "Cluster Drift").sum()),
        Normal_Count=("Final_Status", lambda x: (x == "Normal").sum()),
        Max_Severity=("Severity_Score_0_100", "max"),
    ).reset_index()
    top_outlier_tags = (all_results[all_results["Final_Status"] == "Actual Outlier"]
                        .sort_values(["Timestamp", "Severity_Score_0_100"], ascending=[True, False])
                        .groupby("Timestamp")["Tag"].apply(lambda x: ", ".join(x.head(8))))
    daily_summary["Top_Actual_Outlier_Tags"] = daily_summary["Timestamp"].map(top_outlier_tags).fillna("")

    period_summary = contiguous_periods(
        all_results.assign(Is_Period_Flag=all_results["Final_Status"].isin(["Actual Outlier", "Warning"])),
        flag_col="Is_Period_Flag",
        group_cols=["Tag", "Final_Status"],
    )

    top_peer_rows = []
    for tag, peers in top_peers.items():
        for rank, peer in enumerate(peers, 1):
            top_peer_rows.append({
                "Tag": tag,
                "Cluster_ID": tag_to_cluster.get(tag, ""),
                "Peer_Rank": rank,
                "Peer_Tag": peer,
                "Clean_Correlation": corr.loc[tag, peer] if peer in corr.columns else np.nan,
                "Abs_Clean_Correlation": abs(corr.loc[tag, peer]) if peer in corr.columns else np.nan,
            })
    top_peers_df = pd.DataFrame(top_peer_rows)

    return {
        "All_Results": all_results,
        "Actual_Outliers": actual_outliers,
        "Warnings": warnings,
        "Cluster_Drift_Supported": cluster_drift,
        "Cluster_Daily_Behavior": cluster_daily,
        "Tag_Summary": tag_summary,
        "Daily_Summary": daily_summary,
        "Outlier_Periods": period_summary,
        "Top_Peer_Tags": top_peers_df,
        "Residual_Reference": resid_ref,
    }


# ---------------------------------------------------------------------------
# Optional benchmark comparison
# ---------------------------------------------------------------------------

def load_benchmark(path: str, sheet_name: Optional[str]) -> Optional[pd.DataFrame]:
    if not path or not os.path.exists(path):
        return None
    xl = pd.ExcelFile(path)
    sheet = sheet_name if sheet_name in xl.sheet_names else xl.sheet_names[0]
    b = clean_column_names(pd.read_excel(path, sheet_name=sheet))
    ts = find_column(b, ["Timestamp", "Time", "DateTime", "Date"])
    tag = find_column(b, ["Tag", "Target_Tag", "Variable", "Column", "Parameter"])
    status = find_column(b, ["Final_Status", "Final Status", "Status", "Binary_Status"])
    cls = find_column(b, ["Final_Class", "Final Class", "Class"])
    if ts is None or tag is None or (status is None and cls is None):
        return None
    out = pd.DataFrame({
        "Timestamp": pd.to_datetime(b[ts], errors="coerce"),
        "Tag": b[tag].astype(str).str.strip(),
    })
    if status is not None:
        out["Benchmark_Status"] = b[status].astype(str)
    else:
        out["Benchmark_Status"] = b[cls].astype(str)
    out["Benchmark_Binary"] = np.where(out["Benchmark_Status"].str.lower().str.contains("normal|ok"), "Normal", "Actual Outlier")
    return out.dropna(subset=["Timestamp", "Tag"])


def compare_to_benchmark(all_results: pd.DataFrame, benchmark: Optional[pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    if benchmark is None:
        return {}
    a = all_results[["Timestamp", "Tag", "Final_Status", "Final_Class", "Severity_Score_0_100"]].copy()
    a["Model_Binary"] = np.where(a["Final_Status"].eq("Actual Outlier"), "Actual Outlier", "Normal")
    comp = a.merge(benchmark, on=["Timestamp", "Tag"], how="inner")
    if comp.empty:
        return {}
    tp = ((comp["Benchmark_Binary"] == "Actual Outlier") & (comp["Model_Binary"] == "Actual Outlier")).sum()
    tn = ((comp["Benchmark_Binary"] == "Normal") & (comp["Model_Binary"] == "Normal")).sum()
    fp = ((comp["Benchmark_Binary"] == "Normal") & (comp["Model_Binary"] == "Actual Outlier")).sum()
    fn = ((comp["Benchmark_Binary"] == "Actual Outlier") & (comp["Model_Binary"] == "Normal")).sum()
    summary = pd.DataFrame([{
        "Matched_Rows": len(comp),
        "TP_Both_Actual_Outlier": int(tp),
        "TN_Both_Normal": int(tn),
        "FP_Model_Only": int(fp),
        "FN_Benchmark_Only": int(fn),
        "Accuracy": safe_divide(tp + tn, len(comp)),
        "Precision": safe_divide(tp, tp + fp),
        "Recall": safe_divide(tp, tp + fn),
        "Specificity": safe_divide(tn, tn + fp),
        "False_Positive_Rate": safe_divide(fp, fp + tn),
        "False_Negative_Rate": safe_divide(fn, fn + tp),
    }])
    by_tag = []
    for tag, g in comp.groupby("Tag"):
        tp_t = ((g["Benchmark_Binary"] == "Actual Outlier") & (g["Model_Binary"] == "Actual Outlier")).sum()
        tn_t = ((g["Benchmark_Binary"] == "Normal") & (g["Model_Binary"] == "Normal")).sum()
        fp_t = ((g["Benchmark_Binary"] == "Normal") & (g["Model_Binary"] == "Actual Outlier")).sum()
        fn_t = ((g["Benchmark_Binary"] == "Actual Outlier") & (g["Model_Binary"] == "Normal")).sum()
        by_tag.append({
            "Tag": tag,
            "Rows": len(g),
            "TP": int(tp_t), "TN": int(tn_t), "FP": int(fp_t), "FN": int(fn_t),
            "Precision": safe_divide(tp_t, tp_t + fp_t),
            "Recall": safe_divide(tp_t, tp_t + fn_t),
            "Specificity": safe_divide(tn_t, tn_t + fp_t),
        })
    return {
        "Benchmark_Summary": summary,
        "Benchmark_By_Tag": pd.DataFrame(by_tag).sort_values(["FP", "FN"], ascending=[False, False]),
        "Benchmark_Row_Comparison": comp,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def make_dashboard(summary: Dict, config: Dict) -> pd.DataFrame:
    rows = [
        ["Metric", "Value"],
        ["Method", "Cluster z-score consistency without causal matrix"],
        ["Clean Reference Period", f"{summary['clean_start'].date()} to {summary['clean_end'].date()}"],
        ["Total Rows", summary["total_rows"]],
        ["Total Tags", summary["total_tags"]],
        ["Total Tag-Timestamp Checks", summary["total_checks"]],
        ["Actual Outlier Rows", summary["actual_outlier_rows"]],
        ["Warning Rows", summary["warning_rows"]],
        ["Cluster Drift Supported Rows", summary["cluster_drift_rows"]],
        ["Normal Rows", summary["normal_rows"]],
        ["Actual Outlier Rate", summary["actual_outlier_rate"]],
        ["STD Multiplier", config["std_multiplier"]],
        ["Cluster Residual STD Multiplier", config["cluster_residual_std_multiplier"]],
        ["Peer Support Rule", f"Same direction >= {config['peer_same_direction_support_min']} and peer high fraction >= {config['peer_high_fraction_supported_min']}"] ,
        ["Outlier Rule", "High tag z-score + high tag-vs-cluster residual + weak/opposite peer support"],
    ]
    return pd.DataFrame(rows[1:], columns=rows[0])


def write_outputs(output_file: str, full_csv_zip: str, sheets: Dict[str, pd.DataFrame], config: Dict) -> None:
    # Full CSV ZIP for row-level audit
    with zipfile.ZipFile(full_csv_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, df in sheets.items():
            if df is None or df.empty:
                continue
            csv_bytes = df.to_csv(index=False).encode("utf-8")
            z.writestr(f"{name[:31]}.csv", csv_bytes)

    # Excel with key sheets and bounded All_Results for usability.
    with pd.ExcelWriter(output_file, engine="xlsxwriter", datetime_format="yyyy-mm-dd") as writer:
        workbook = writer.book
        header_fmt = workbook.add_format({"bold": True, "bg_color": "#1F4E78", "font_color": "white", "border": 1})
        normal_fmt = workbook.add_format({"border": 1})
        pct_fmt = workbook.add_format({"num_format": "0.00%", "border": 1})
        num_fmt = workbook.add_format({"num_format": "0.00", "border": 1})
        date_fmt = workbook.add_format({"num_format": "yyyy-mm-dd", "border": 1})
        red_fmt = workbook.add_format({"bg_color": "#F4CCCC"})
        yellow_fmt = workbook.add_format({"bg_color": "#FFF2CC"})
        green_fmt = workbook.add_format({"bg_color": "#D9EAD3"})

        ordered = [
            "Dashboard", "Rules", "Clean_Period_Candidates", "Clean_Detection_Daily", "Tag_Reference_Profile",
            "Learned_Clusters", "Top_Peer_Tags", "Cluster_Daily_Behavior", "Tag_Summary", "Daily_Summary",
            "Actual_Outliers", "Warnings", "Cluster_Drift_Supported", "Outlier_Periods", "All_Results_Sample",
            "Benchmark_Summary", "Benchmark_By_Tag", "Benchmark_Row_Comparison"
        ]
        for name in ordered:
            df = sheets.get(name)
            if df is None or df.empty:
                continue
            out_df = df.copy()
            if name == "All_Results_Sample" and len(out_df) > config["excel_all_results_max_rows"]:
                out_df = out_df.head(config["excel_all_results_max_rows"])
            if name in ["Actual_Outliers", "Warnings", "Cluster_Drift_Supported"] and len(out_df) > config["excel_top_outliers_max_rows"]:
                out_df = out_df.head(config["excel_top_outliers_max_rows"])
            out_df.to_excel(writer, sheet_name=name[:31], index=False)
            ws = writer.sheets[name[:31]]
            # Header format and freeze panes
            for col_num, value in enumerate(out_df.columns):
                ws.write(0, col_num, value, header_fmt)
                width = min(max(12, len(str(value)) + 2), 35)
                ws.set_column(col_num, col_num, width)
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, len(out_df), len(out_df.columns) - 1)
            # Date columns
            for col_num, col in enumerate(out_df.columns):
                lc = str(col).lower()
                if "timestamp" in lc or "date" in lc:
                    ws.set_column(col_num, col_num, 14, date_fmt)
                elif "rate" in lc or "fraction" in lc:
                    ws.set_column(col_num, col_num, 14, pct_fmt)
                elif "z" in lc or "score" in lc or "severity" in lc or "count" in lc:
                    ws.set_column(col_num, col_num, 14, num_fmt)
            # Simple conditional formatting on status/class columns
            if "Final_Status" in out_df.columns:
                c = out_df.columns.get_loc("Final_Status")
                rng = (1, c, len(out_df), c)
                ws.conditional_format(*rng, {"type": "text", "criteria": "containing", "value": "Actual Outlier", "format": red_fmt})
                ws.conditional_format(*rng, {"type": "text", "criteria": "containing", "value": "Warning", "format": yellow_fmt})
                ws.conditional_format(*rng, {"type": "text", "criteria": "containing", "value": "Normal", "format": green_fmt})
        # Dashboard chart if sheets exist
        if "Daily_Summary" in sheets and not sheets["Daily_Summary"].empty and "Dashboard" in writer.sheets:
            ws = writer.sheets["Dashboard"]
            daily = sheets["Daily_Summary"]
            # Daily_Summary sheet has data from row 2. Add a chart referencing it.
            chart = workbook.add_chart({"type": "line"})
            d_sheet = "Daily_Summary"
            n = min(len(daily), 300)
            if n > 1:
                chart.add_series({
                    "name": "Actual Outliers",
                    "categories": f"='{d_sheet}'!$A$2:$A${n+1}",
                    "values": f"='{d_sheet}'!$B$2:$B${n+1}",
                })
                chart.add_series({
                    "name": "Warnings",
                    "categories": f"='{d_sheet}'!$A$2:$A${n+1}",
                    "values": f"='{d_sheet}'!$C$2:$C${n+1}",
                })
                chart.set_title({"name": "Daily Actual Outliers and Warnings"})
                chart.set_x_axis({"date_axis": True})
                chart.set_y_axis({"name": "Count"})
                ws.insert_chart("D2", chart, {"x_scale": 1.5, "y_scale": 1.2})


def main(config: Optional[Dict] = None) -> Dict[str, pd.DataFrame]:
    if config is None:
        config = CONFIG.copy()

    df, ts, tag_cols = load_process_data(config)
    clean_daily, clean_candidates, clean_mask = detect_clean_period(df, ts, tag_cols, config)
    ref = build_reference_profile(df, tag_cols, clean_mask)
    cluster_df, cluster_map, corr_pairs = build_clusters_from_clean(df, tag_cols, clean_mask, config)

    results = run_cluster_consistency_model(df, ts, tag_cols, clean_mask, ref, cluster_df, cluster_map, config)

    total_checks = len(results["All_Results"])
    actual_count = int((results["All_Results"]["Final_Status"] == "Actual Outlier").sum())
    warning_count = int((results["All_Results"]["Final_Status"] == "Warning").sum())
    cluster_drift_count = int((results["All_Results"]["Final_Status"] == "Cluster Drift").sum())
    normal_count = int((results["All_Results"]["Final_Status"] == "Normal").sum())

    selected = clean_candidates.iloc[0]
    summary_info = {
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
        {"Rule": "Clean period", "Value": "Longest/lowest-score stable window from historical data"},
        {"Rule": "Cluster creation", "Value": "Abs Pearson correlation >= cluster_abs_corr_threshold on clean data only"},
        {"Rule": "Tag deviation", "Value": "Conservative min(abs(std_z), abs(robust_z)) using clean period"},
        {"Rule": "Outlier", "Value": "Tag high z-score AND high tag-vs-peer residual AND weak/opposite peer support"},
        {"Rule": "Cluster drift", "Value": "Most peers high in same direction; not counted as isolated outlier"},
        {"Rule": "Warning", "Value": "Borderline mismatch or mixed high/low behaviour inside cluster"},
        {"Rule": "std_multiplier", "Value": config["std_multiplier"]},
        {"Rule": "cluster_residual_std_multiplier", "Value": config["cluster_residual_std_multiplier"]},
        {"Rule": "peer_same_direction_support_min", "Value": config["peer_same_direction_support_min"]},
        {"Rule": "peer_high_fraction_supported_min", "Value": config["peer_high_fraction_supported_min"]},
        {"Rule": "peer_high_fraction_isolated_max", "Value": config["peer_high_fraction_isolated_max"]},
    ])

    sheets = {
        "Dashboard": make_dashboard(summary_info, config),
        "Rules": rules,
        "Clean_Period_Candidates": clean_candidates,
        "Clean_Detection_Daily": clean_daily,
        "Tag_Reference_Profile": ref,
        "Learned_Clusters": cluster_df,
        "Clean_Correlation_Pairs": corr_pairs,
        **results,
        "All_Results_Sample": results["All_Results"].sort_values(["Severity_Score_0_100", "Timestamp"], ascending=[False, True]),
    }

    benchmark = load_benchmark(config.get("benchmark_file", ""), config.get("benchmark_sheet_name", "All_Results"))
    comp_sheets = compare_to_benchmark(results["All_Results"], benchmark)
    sheets.update(comp_sheets)

    write_outputs(config["output_file"], config["full_csv_zip"], sheets, config)

    print("Completed cluster z-score consistency outlier detection.")
    print(f"Clean period: {summary_info['clean_start']} to {summary_info['clean_end']}")
    print(f"Actual outliers: {actual_count} / {total_checks} ({summary_info['actual_outlier_rate']:.2%})")
    print(f"Excel: {config['output_file']}")
    print(f"Full CSV ZIP: {config['full_csv_zip']}")
    return sheets


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cluster z-score consistency outlier detection without causal matrix")
    p.add_argument("--data_file", default=CONFIG["data_file"])
    p.add_argument("--data_sheet_name", default=CONFIG["data_sheet_name"])
    p.add_argument("--timestamp_col", default=CONFIG["timestamp_col"])
    p.add_argument("--output_file", default=CONFIG["output_file"])
    p.add_argument("--full_csv_zip", default=CONFIG["full_csv_zip"])
    p.add_argument("--benchmark_file", default="")
    p.add_argument("--benchmark_sheet_name", default="All_Results")
    p.add_argument("--std_multiplier", type=float, default=CONFIG["std_multiplier"])
    p.add_argument("--strong_std_multiplier", type=float, default=CONFIG["strong_std_multiplier"])
    p.add_argument("--cluster_residual_std_multiplier", type=float, default=CONFIG["cluster_residual_std_multiplier"])
    p.add_argument("--warning_std_multiplier", type=float, default=CONFIG["warning_std_multiplier"])
    p.add_argument("--cluster_abs_corr_threshold", type=float, default=CONFIG["cluster_abs_corr_threshold"])
    p.add_argument("--peer_same_direction_support_min", type=float, default=CONFIG["peer_same_direction_support_min"])
    p.add_argument("--peer_high_fraction_supported_min", type=float, default=CONFIG["peer_high_fraction_supported_min"])
    p.add_argument("--peer_high_fraction_isolated_max", type=float, default=CONFIG["peer_high_fraction_isolated_max"])
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    cfg = CONFIG.copy()
    cfg.update(vars(args))
    main(cfg)
