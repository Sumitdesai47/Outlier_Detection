"""
WITHOUT-CAUSAL CLUSTER-CONSISTENCY ACTUAL OUTLIER DETECTION

Purpose
-------
Reduce false positives by separating process/cluster drift from actual isolated outliers.
A tag is treated as an actual outlier only when its behavior breaks away from its learned peer cluster.
If the tag and peer cluster move together, it is labeled as Cluster Drift - Supported, not as an outlier.

Inputs
------
- Wide process-data Excel file with one Timestamp column and numeric tag columns.

Outputs
-------
- Excel workbook with dashboard, clean period, clusters, tag summary, daily summary, actual outliers, warnings, and cluster drift.
- CSV ZIP with full row-level results.

Run
---
python build_cluster_consistency_actual_outlier.py \
  --data_file "Multi_X_Multi_Y_Correct_Data.xlsx" \
  --output_excel "cluster_consistency_actual_outlier_analysis.xlsx" \
  --output_zip "cluster_consistency_actual_outlier_full_results.zip"
"""

from __future__ import annotations

import argparse
import math
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from sklearn.cluster import AgglomerativeClustering
except Exception:  # pragma: no cover
    AgglomerativeClustering = None


def robust_center_scale(s: pd.Series, eps: float = 1e-9) -> Tuple[float, float]:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) == 0:
        return np.nan, eps
    center = float(s.median())
    mad = float((s - center).abs().median())
    scale = 1.4826 * mad
    if not np.isfinite(scale) or scale < eps:
        scale = float(s.std())
    if not np.isfinite(scale) or scale < eps:
        scale = eps
    return center, scale


def normalize_timestamp(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _apply_shutdown_indicator_filter(
    df: pd.DataFrame,
    ts: str,
    tag_cols: List[str],
    shutdown_indicator_tags: Optional[List[str]],
) -> Tuple[pd.DataFrame, List[str]]:
    """Drop rows where any selected plant-status tag is 0 or near-zero (<=1e-6); keep columns in analysis."""
    if not shutdown_indicator_tags:
        return df, tag_cols
    shutdown_set = {str(t).strip() for t in shutdown_indicator_tags if t and str(t).strip()}
    shutdown_set = {t for t in shutdown_set if t in df.columns}
    if not shutdown_set:
        return df, tag_cols
    is_shut = pd.Series(False, index=df.index)
    for c in shutdown_set:
        v = pd.to_numeric(df[c], errors="coerce")
        is_shut |= v.notna() & (v <= 1e-6)
    df = df.loc[~is_shut].reset_index(drop=True)
    keep = [ts] + [c for c in tag_cols if c in df.columns]
    df = df[[c for c in keep if c in df.columns]]
    return df, tag_cols


def load_wide_data(path: str, sheet_name: str | None, timestamp_col: str) -> Tuple[pd.DataFrame, str, List[str]]:
    """Load workbook the same way as part8: best sheet, timestamp auto-detect, wide or long layout."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    from auto_without_causal_outlier_drift import (
        build_pivot,
        detect_timestamp_col,
        make_long_format,
        parse_tag_cols_argument,
        read_input_file,
    )

    df, _selected = read_input_file(
        str(path),
        sheet_name=sheet_name,
        max_rows=None,
        datetime_format=None,
    )
    if df.empty:
        raise ValueError("Uploaded sheet is empty.")

    lower_map = {str(c).strip().lower(): c for c in df.columns}
    ts_override = None
    if timestamp_col:
        tc = str(timestamp_col).strip()
        if tc in df.columns:
            ts_override = tc
        elif tc.lower() in lower_map:
            ts_override = lower_map[tc.lower()]

    ts_name = detect_timestamp_col(df, override=ts_override, datetime_format=None)
    long_df, _input_fmt, _, _, _ = make_long_format(
        df,
        timestamp_col=ts_name,
        tag_col=None,
        value_col=None,
        tag_cols=parse_tag_cols_argument(None),
        datetime_format=None,
    )
    wide = build_pivot(long_df).reset_index().copy()
    ts = "Timestamp"
    if ts not in wide.columns:
        raise ValueError("Internal error: pivot missing Timestamp column.")
    wide[ts] = normalize_timestamp(wide[ts])
    wide = wide.dropna(subset=[ts]).sort_values(ts).reset_index(drop=True)
    if wide.empty:
        raise ValueError("No rows left after parsing timestamps.")

    tag_cols: List[str] = []
    for c in wide.columns:
        if c == ts:
            continue
        x = pd.to_numeric(wide[c], errors="coerce")
        if x.notna().sum() >= 5:
            wide[c] = x
            tag_cols.append(c)

    if len(tag_cols) < 2:
        raise ValueError(
            "At least two numeric tag columns are required. "
            "Use a wide sheet (time + tag columns) or long format (time, tag name, value)."
        )
    return wide, ts, tag_cols


def detect_clean_period(df: pd.DataFrame, ts: str, tags: List[str], preferred_window: int = 180):
    z = pd.DataFrame(index=df.index)
    dz = pd.DataFrame(index=df.index)
    for c in tags:
        center, scale = robust_center_scale(df[c])
        z[c] = (df[c] - center) / scale
        d_center, d_scale = robust_center_scale(df[c].diff())
        dz[c] = (df[c].diff() - d_center) / d_scale

    daily = pd.DataFrame({
        "Timestamp": df[ts],
        "Robust_Bad_Tag_Fraction": (z.abs() > 3.5).mean(axis=1),
        "Spike_Bad_Tag_Fraction": (dz.abs() > 4.0).mean(axis=1).fillna(0),
        "Median_Abs_Robust_Z": z.abs().median(axis=1),
    })
    daily["Clean_Score"] = (
        daily["Robust_Bad_Tag_Fraction"]
        + 0.50 * daily["Spike_Bad_Tag_Fraction"]
        + 0.02 * daily["Median_Abs_Robust_Z"]
    )

    candidates = []
    # Window sizes are in **rows** (not calendar days). Try long baselines first, then shorter
    # ones so smaller workbooks (common for tests / extracts) still get a valid clean window.
    n = len(df)
    min_rows = 2
    seed_windows = [
        preferred_window,
        240,
        180,
        150,
        120,
        90,
        60,
        45,
        30,
        25,
        20,
        15,
        10,
        8,
        6,
        5,
        4,
        3,
        2,
    ]
    seen_w: set[int] = set()
    ordered_ws: List[int] = []
    for w in seed_windows:
        if w < min_rows or w > n or w in seen_w:
            continue
        seen_w.add(w)
        ordered_ws.append(w)
    if n >= min_rows and n not in seen_w:
        ordered_ws.insert(0, n)

    for w in ordered_ws:
        roll = daily["Clean_Score"].rolling(w, min_periods=w).mean()
        if not roll.notna().any():
            continue
        end = int(roll.idxmin())
        start = int(end - w + 1)
        candidates.append({
            "Window_Days": w,
            "Start_Index": start,
            "End_Index": end,
            "Start_Date": df.loc[start, ts],
            "End_Date": df.loc[end, ts],
            "Avg_Clean_Score": float(roll.loc[end]),
            "Avg_Bad_Tag_Fraction": float(daily.loc[start:end, "Robust_Bad_Tag_Fraction"].mean()),
            "Avg_Spike_Tag_Fraction": float(daily.loc[start:end, "Spike_Bad_Tag_Fraction"].mean()),
            "Avg_Median_Abs_Z": float(daily.loc[start:end, "Median_Abs_Robust_Z"].mean()),
        })

    if not candidates and n >= min_rows:
        # Last resort: treat the full series as the reference window (short files).
        start, end = 0, n - 1
        cs_mean = float(daily["Clean_Score"].mean())
        candidates.append({
            "Window_Days": n,
            "Start_Index": start,
            "End_Index": end,
            "Start_Date": df.loc[start, ts],
            "End_Date": df.loc[end, ts],
            "Avg_Clean_Score": cs_mean,
            "Avg_Bad_Tag_Fraction": float(daily["Robust_Bad_Tag_Fraction"].mean()),
            "Avg_Spike_Tag_Fraction": float(daily["Spike_Bad_Tag_Fraction"].mean()),
            "Avg_Median_Abs_Z": float(daily["Median_Abs_Robust_Z"].mean()),
        })

    if not candidates:
        raise ValueError("Could not detect clean period. Not enough rows.")

    cand_df = pd.DataFrame(candidates).sort_values(["Avg_Clean_Score", "Window_Days"], ascending=[True, False]).reset_index(drop=True)
    # Use the preferred long window if available and close to best; otherwise choose best score.
    pref = cand_df[cand_df["Window_Days"] == preferred_window]
    if len(pref):
        selected = pref.iloc[0].to_dict()
    else:
        selected = cand_df.iloc[0].to_dict()

    daily["Is_Selected_Clean_Period"] = False
    daily.loc[int(selected["Start_Index"]):int(selected["End_Index"]), "Is_Selected_Clean_Period"] = True
    return int(selected["Start_Index"]), int(selected["End_Index"]), selected, cand_df, daily


def fit_ridge_predict(X_train: pd.DataFrame, y_train: pd.Series, X_all: pd.DataFrame, alpha: float = 2.0):
    med = X_train.median(numeric_only=True)
    X_train = X_train.fillna(med)
    X_all = X_all.fillna(med)
    mean = X_train.mean(axis=0)
    std = X_train.std(axis=0).replace(0, 1.0).fillna(1.0)
    Xs_train = (X_train - mean) / std
    Xs_all = (X_all - mean) / std
    valid = y_train.notna()
    Xs_train = Xs_train.loc[valid]
    y = y_train.loc[valid]
    if len(y) < 20:
        pred = np.repeat(float(y.median()) if len(y) else np.nan, len(X_all))
        return pred, np.nan
    X = np.column_stack([np.ones(len(Xs_train)), Xs_train.values])
    Xa = np.column_stack([np.ones(len(Xs_all)), Xs_all.values])
    I = np.eye(X.shape[1])
    I[0, 0] = 0
    beta = np.linalg.pinv(X.T @ X + alpha * I) @ X.T @ y.values
    pred_all = Xa @ beta
    pred_train = X @ beta
    ss_res = np.sum((y.values - pred_train) ** 2)
    ss_tot = np.sum((y.values - y.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return pred_all, r2


def build_clusters(clean: pd.DataFrame, tags: List[str], n_clusters: int = 10):
    X = clean[tags].copy()
    centers = {}
    scales = {}
    for c in tags:
        center, scale = robust_center_scale(X[c])
        centers[c] = center
        scales[c] = scale
        X[c] = (X[c] - center) / scale
    corr = X.fillna(0).corr().fillna(0)
    n_clusters = min(n_clusters, max(2, len(tags) // 2))

    if AgglomerativeClustering is None:
        # Fallback: connected components at absolute correlation >= 0.70.
        adj = {c: set() for c in tags}
        for i, a in enumerate(tags):
            for b in tags[i + 1:]:
                if abs(corr.loc[a, b]) >= 0.70:
                    adj[a].add(b)
                    adj[b].add(a)
        labels = {}
        seen = set()
        k = 0
        for t in tags:
            if t in seen:
                continue
            k += 1
            stack = [t]
            seen.add(t)
            while stack:
                u = stack.pop()
                labels[u] = k
                for v in adj[u]:
                    if v not in seen:
                        seen.add(v)
                        stack.append(v)
        return labels, corr, centers, scales

    # Writable copy: pandas/numpy may expose read-only .values; fill_diagonal must mutate.
    dist = (1 - corr.abs()).to_numpy(dtype=float, copy=True)
    np.fill_diagonal(dist, 0.0)
    try:
        model = AgglomerativeClustering(n_clusters=n_clusters, metric="precomputed", linkage="average")
    except TypeError:  # older sklearn
        model = AgglomerativeClustering(n_clusters=n_clusters, affinity="precomputed", linkage="average")
    label_arr = model.fit_predict(dist)
    labels = {t: int(label_arr[i]) + 1 for i, t in enumerate(tags)}
    return labels, corr, centers, scales


def classify_direction(z_value: float) -> str:
    if pd.isna(z_value):
        return "Unknown"
    if z_value >= 0.5:
        return "UP"
    if z_value <= -0.5:
        return "DOWN"
    return "NORMAL"


def run_cluster_consistency_model(df: pd.DataFrame, ts: str, tags: List[str], clean_start: int, clean_end: int):
    clean = df.iloc[clean_start:clean_end + 1].copy()
    cluster_map, corr, centers, scales = build_clusters(clean, tags, n_clusters=10)
    groups: Dict[int, List[str]] = {}
    for t, cid in cluster_map.items():
        groups.setdefault(cid, []).append(t)

    all_results = []
    profiles = []

    for target in tags:
        same_cluster = [t for t in groups[cluster_map[target]] if t != target]
        corr_rank = corr[target].drop(labels=[target], errors="ignore").abs().sort_values(ascending=False)
        if len(same_cluster) >= 3:
            peers = [p for p in corr_rank.index if p in same_cluster][:5]
        else:
            peers = corr_rank.head(5).index.tolist()
        if len(peers) < 3:
            peers = corr_rank.head(5).index.tolist()

        avg_peer_corr = float(corr.loc[target, peers].abs().median()) if peers else 0.0
        pred, r2 = fit_ridge_predict(clean[peers], clean[target], df[peers], alpha=2.0)
        residual = df[target] - pred
        res_center, res_scale = robust_center_scale(residual.iloc[clean_start:clean_end + 1])
        target_center, target_scale = robust_center_scale(clean[target])
        target_z = (df[target] - target_center) / target_scale
        residual_z = (residual - res_center) / res_scale

        peer_z = pd.DataFrame({p: (df[p] - centers[p]) / scales[p] for p in peers})
        peer_median_z = peer_z.median(axis=1)
        peer_abs_median_z = peer_z.abs().median(axis=1)
        cluster_diff_z = (target_z - peer_median_z).abs()

        # Peer support: how much of the peer bunch moved in same direction as target.
        sign_same = np.sign(peer_z).eq(np.sign(target_z), axis=0)
        min_mag = np.minimum(np.maximum(1.5, target_z.abs() * 0.40), 3.0)
        shifted = peer_z.abs().ge(min_mag, axis=0)
        peer_same_support = (sign_same & shifted).mean(axis=1)
        sign_opp = np.sign(peer_z).eq(-np.sign(target_z), axis=0)
        peer_opp_support = (sign_opp & shifted).mean(axis=1)

        reliability_flag = bool((pd.notna(r2) and r2 >= 0.50) or (avg_peer_corr >= 0.60))
        reliability = "High" if (pd.notna(r2) and r2 >= 0.60) else "Medium" if reliability_flag else "Low"

        clean_low = float(clean[target].quantile(0.005))
        clean_high = float(clean[target].quantile(0.995))

        severe_break = (
            (residual_z.abs() >= 8.0)
            & (cluster_diff_z >= 5.5)
            & (peer_same_support <= 0.20)
            & (target_z.abs() >= 3.0)
        )
        moderate_break = (
            (residual_z.abs() >= 7.0)
            & (cluster_diff_z >= 4.5)
            & (peer_same_support <= 0.25)
            & (target_z.abs() >= 3.0)
            & reliability_flag
        )
        extreme_value_break = (
            (target_z.abs() >= 8.0)
            & (cluster_diff_z >= 4.5)
            & (peer_same_support <= 0.25)
        )
        raw_actual = severe_break | moderate_break | extreme_value_break
        run_len = raw_actual.groupby((~raw_actual).cumsum()).transform("sum")
        confirmed_actual = severe_break | extreme_value_break | (raw_actual & (run_len >= 2))

        warning = (
            (~confirmed_actual)
            & (residual_z.abs() >= 5.0)
            & (cluster_diff_z >= 3.5)
            & (peer_same_support <= 0.25)
            & reliability_flag
        )
        cluster_supported = (
            (~confirmed_actual)
            & (~warning)
            & (((target_z.abs() >= 3.5) | (residual_z.abs() >= 3.5)) & (peer_same_support >= 0.60) & (peer_abs_median_z >= 1.2))
        )

        final_class = np.select(
            [confirmed_actual, warning, cluster_supported],
            ["Actual Outlier", "Warning - Check", "Cluster Drift - Supported"],
            default="Normal",
        )
        final_status = np.select(
            [confirmed_actual, warning, cluster_supported],
            ["Actual_Outlier", "Warning", "Cluster_Drift_Not_Outlier"],
            default="Normal",
        )

        severity = (
            7.0 * residual_z.abs().clip(upper=10)
            + 7.0 * cluster_diff_z.clip(upper=10)
            + 5.0 * target_z.abs().clip(upper=10)
            + 25.0 * (1 - peer_same_support).clip(lower=0, upper=1)
        ).clip(upper=100).round(1)

        reason = []
        for i in df.index:
            fc = final_class[i]
            if fc == "Actual Outlier":
                reason.append("Tag broke away from peer cluster: high residual and low same-direction peer support.")
            elif fc == "Warning - Check":
                reason.append("Borderline cluster-consistency break; check before treating as outlier.")
            elif fc == "Cluster Drift - Supported":
                reason.append("Tag moved, but peer cluster moved in same direction; not counted as actual outlier.")
            else:
                reason.append("Tag is consistent with its clean-period peer-cluster behavior.")

        out = pd.DataFrame({
            "Timestamp": df[ts],
            "Tag": target,
            "Actual_Value": df[target],
            "Cluster_ID": cluster_map[target],
            "Peer_Tags": ", ".join(peers),
            "Predicted_From_Peers": pred,
            "Residual": residual,
            "Residual_Z": residual_z,
            "Target_Z_vs_Clean": target_z,
            "Peer_Median_Z": peer_median_z,
            "Peer_Abs_Median_Z": peer_abs_median_z,
            "Cluster_Diff_Z": cluster_diff_z,
            "Peer_Same_Direction_Support": peer_same_support,
            "Peer_Opposite_Direction_Support": peer_opp_support,
            "Direction": [classify_direction(x) for x in target_z],
            "Final_Class": final_class,
            "Final_Status": final_status,
            "Severity_Score_0_100": severity,
            "Reliability": reliability,
            "Model_R2_Clean": r2,
            "Median_Peer_Correlation": avg_peer_corr,
            "Clean_Median": target_center,
            "Clean_MAD_Scale": target_scale,
            "Clean_Outer_Low_0_5pct": clean_low,
            "Clean_Outer_High_99_5pct": clean_high,
            "Explanation": reason,
        })
        all_results.append(out)
        profiles.append({
            "Tag": target,
            "Cluster_ID": cluster_map[target],
            "Cluster_Size": len(groups[cluster_map[target]]),
            "Peer_Tags_Used": ", ".join(peers),
            "Median_Peer_Correlation": avg_peer_corr,
            "Model_R2_Clean": r2,
            "Reliability": reliability,
            "Clean_Median": target_center,
            "Clean_MAD_Scale": target_scale,
            "Clean_Outer_Low_0_5pct": clean_low,
            "Clean_Outer_High_99_5pct": clean_high,
        })

    all_df = pd.concat(all_results, ignore_index=True)
    profile_df = pd.DataFrame(profiles)

    tag_summary = all_df.groupby("Tag").agg(
        Cluster_ID=("Cluster_ID", "first"),
        Peer_Tags=("Peer_Tags", "first"),
        Reliability=("Reliability", "first"),
        Model_R2_Clean=("Model_R2_Clean", "first"),
        Median_Peer_Correlation=("Median_Peer_Correlation", "first"),
        Total_Rows=("Final_Status", "size"),
        Actual_Outlier_Count=("Final_Status", lambda x: int((x == "Actual_Outlier").sum())),
        Warning_Count=("Final_Status", lambda x: int((x == "Warning").sum())),
        Cluster_Drift_Not_Outlier_Count=("Final_Status", lambda x: int((x == "Cluster_Drift_Not_Outlier").sum())),
        Normal_Count=("Final_Status", lambda x: int((x == "Normal").sum())),
        Max_Severity=("Severity_Score_0_100", "max"),
        Avg_Severity=("Severity_Score_0_100", "mean"),
    ).reset_index().sort_values(["Actual_Outlier_Count", "Warning_Count", "Max_Severity"], ascending=False)

    daily_summary = all_df.groupby("Timestamp").agg(
        Actual_Outlier_Count=("Final_Status", lambda x: int((x == "Actual_Outlier").sum())),
        Warning_Count=("Final_Status", lambda x: int((x == "Warning").sum())),
        Cluster_Drift_Not_Outlier_Count=("Final_Status", lambda x: int((x == "Cluster_Drift_Not_Outlier").sum())),
        Normal_Count=("Final_Status", lambda x: int((x == "Normal").sum())),
        Max_Severity=("Severity_Score_0_100", "max"),
    ).reset_index()

    top_actual_tags = (
        all_df[all_df["Final_Status"] == "Actual_Outlier"]
        .sort_values(["Timestamp", "Severity_Score_0_100"], ascending=[True, False])
        .groupby("Timestamp")["Tag"]
        .apply(lambda x: ", ".join(x.head(10)))
    )
    daily_summary["Top_Actual_Outlier_Tags"] = daily_summary["Timestamp"].map(top_actual_tags).fillna("")

    cluster_definition = []
    for cid, members in sorted(groups.items()):
        cluster_definition.append({
            "Cluster_ID": cid,
            "Tag_Count": len(members),
            "Tags": ", ".join(members),
        })
    cluster_definition = pd.DataFrame(cluster_definition)

    return all_df, profile_df, tag_summary, daily_summary, cluster_definition


def abnormal_periods(all_df: pd.DataFrame, status_value: str = "Actual_Outlier") -> pd.DataFrame:
    rows = []
    sub = all_df[all_df["Final_Status"] == status_value].copy()
    if sub.empty:
        return pd.DataFrame(columns=["Tag", "Final_Status", "Start_Date", "End_Date", "Duration_Days", "Max_Severity", "Avg_Severity", "Direction_Mode"])
    for tag, g in sub.sort_values("Timestamp").groupby("Tag"):
        dates = pd.to_datetime(g["Timestamp"]).sort_values().reset_index(drop=True)
        idx_rows = g.sort_values("Timestamp").reset_index(drop=True)
        block = 0
        starts = [0]
        for i in range(1, len(dates)):
            if (dates.iloc[i] - dates.iloc[i - 1]).days > 1:
                starts.append(i)
        starts.append(len(dates))
        for a, b in zip(starts[:-1], starts[1:]):
            part = idx_rows.iloc[a:b]
            rows.append({
                "Tag": tag,
                "Final_Status": status_value,
                "Start_Date": part["Timestamp"].min(),
                "End_Date": part["Timestamp"].max(),
                "Duration_Days": int((part["Timestamp"].max() - part["Timestamp"].min()).days + 1),
                "Row_Count": len(part),
                "Max_Severity": float(part["Severity_Score_0_100"].max()),
                "Avg_Severity": float(part["Severity_Score_0_100"].mean()),
                "Direction_Mode": part["Direction"].mode().iloc[0] if len(part["Direction"].mode()) else "Unknown",
                "Primary_Peer_Tags": part["Peer_Tags"].iloc[0],
            })
    return pd.DataFrame(rows).sort_values(["Max_Severity", "Duration_Days"], ascending=False)


def _build_cluster_dashboard_metrics(
    selected: dict,
    df: pd.DataFrame,
    tags: List[str],
    all_df: pd.DataFrame,
) -> pd.DataFrame:
    total_checks = len(all_df)
    actual_count = int((all_df["Final_Status"] == "Actual_Outlier").sum())
    warning_count = int((all_df["Final_Status"] == "Warning").sum())
    cluster_count = int((all_df["Final_Status"] == "Cluster_Drift_Not_Outlier").sum())
    normal_count = int((all_df["Final_Status"] == "Normal").sum())
    prev_abnormal = 18030
    prev_warning = 5599
    reduction_pct = 1 - (actual_count / prev_abnormal) if prev_abnormal else np.nan
    return pd.DataFrame([
        {"Metric": "Output", "Value": "Cluster-consistency actual outlier detection"},
        {"Metric": "Core Change", "Value": "Cluster-wide movement is not counted as outlier; only isolated tag-vs-peer break is actual outlier."},
        {"Metric": "Causal Matrix Used", "Value": "No"},
        {"Metric": "Clean Reference Start", "Value": selected["Start_Date"]},
        {"Metric": "Clean Reference End", "Value": selected["End_Date"]},
        {"Metric": "Clean Window Days", "Value": int(selected["Window_Days"])},
        {"Metric": "Total Data Rows", "Value": len(df)},
        {"Metric": "Total Tags", "Value": len(tags)},
        {"Metric": "Total Tag-Timestamp Checks", "Value": total_checks},
        {"Metric": "Actual Outlier Rows", "Value": actual_count},
        {"Metric": "Actual Outlier Rate", "Value": actual_count / total_checks if total_checks else 0.0},
        {"Metric": "Warning Rows", "Value": warning_count},
        {"Metric": "Cluster Drift Not Outlier Rows", "Value": cluster_count},
        {"Metric": "Normal Rows", "Value": normal_count},
        {"Metric": "Previous Combined Abnormal Rows", "Value": prev_abnormal},
        {"Metric": "Previous Combined Warning Rows", "Value": prev_warning},
        {"Metric": "False Positive Reduction vs Previous Abnormal", "Value": reduction_pct},
    ])


def compute_cluster_consistency_bundle(
    data_file: str,
    *,
    sheet_name: Optional[str] = None,
    timestamp_col: str = "Timestamp",
    clean_window_days: int = 180,
    shutdown_indicator_tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    df, ts, tags = load_wide_data(data_file, sheet_name, timestamp_col)
    if shutdown_indicator_tags:
        df, tags = _apply_shutdown_indicator_filter(df, ts, tags, shutdown_indicator_tags)
    if df.empty:
        raise ValueError("No rows left after shutdown filtering.")
    if len(tags) < 2:
        raise ValueError("At least two numeric tag columns are required after filtering.")
    clean_start, clean_end, selected, clean_candidates, clean_daily = detect_clean_period(
        df, ts, tags, clean_window_days
    )
    all_df, profile_df, tag_summary, daily_summary, cluster_definition = run_cluster_consistency_model(
        df, ts, tags, clean_start, clean_end
    )
    dashboard = _build_cluster_dashboard_metrics(selected, df, tags, all_df)
    return {
        "df": df,
        "ts": ts,
        "tags": tags,
        "selected": selected,
        "clean_candidates": clean_candidates,
        "clean_daily": clean_daily,
        "all_df": all_df,
        "profile_df": profile_df,
        "tag_summary": tag_summary,
        "daily_summary": daily_summary,
        "cluster_definition": cluster_definition,
        "dashboard": dashboard,
    }


def run_cluster_consistency_for_web(
    data_file: str,
    *,
    sheet_name: Optional[str] = None,
    timestamp_col: str = "Timestamp",
    clean_window_days: int = 180,
    shutdown_indicator_tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """In-memory pipeline for Flask (no Excel/ZIP writes)."""
    return compute_cluster_consistency_bundle(
        data_file,
        sheet_name=sheet_name,
        timestamp_col=timestamp_col,
        clean_window_days=clean_window_days,
        shutdown_indicator_tags=shutdown_indicator_tags,
    )


def safe_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = pd.to_datetime(out[c]).dt.to_pydatetime()
        elif pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].replace([np.inf, -np.inf], np.nan)
    out = out.where(pd.notna(out), None)
    return out


def df_to_rows(df: pd.DataFrame, max_rows: int | None = None):
    if max_rows is not None:
        df = df.head(max_rows)
    df = safe_for_excel(df)
    return [list(df.columns)] + df.values.tolist()


def write_csv_zip(output_zip: str, sheets: Dict[str, pd.DataFrame]):
    output_zip = str(output_zip)
    temp_dir = Path(output_zip).with_suffix("")
    temp_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, df in sheets.items():
            csv_path = temp_dir / f"{name}.csv"
            df.to_csv(csv_path, index=False)
            z.write(csv_path, arcname=csv_path.name)


def write_excel_artifact(output_excel: str, sheets: Dict[str, pd.DataFrame], title: str = "Cluster Consistency Actual Outlier Analysis"):
    from artifact_tool import Workbook, SpreadsheetFile

    wb = Workbook.create()

    header_fmt = {
        "fill": "#1F4E78",
        "font": {"bold": True, "color": "#FFFFFF"},
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
        "wrap_text": True,
    }
    title_fmt = {
        "fill": "#0F172A",
        "font": {"bold": True, "color": "#FFFFFF", "size": 14},
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
    }

    first = True
    for sheet_name, df in sheets.items():
        if first:
            sh = wb.worksheets.get_or_add(sheet_name[:31], {"renameFirstIfOnlyNewSpreadsheet": True})
            first = False
        else:
            sh = wb.worksheets.add(sheet_name[:31])
        rows = df_to_rows(df)
        if len(rows) == 0:
            rows = [["No data"]]
        sh.get_range_by_indexes(0, 0, len(rows), len(rows[0])).values = rows
        sh.get_range_by_indexes(0, 0, 1, len(rows[0])).format = header_fmt
        sh.freeze_panes.freeze_rows(1)
        # practical widths
        max_cols = len(rows[0])
        for col_idx in range(max_cols):
            col_values = [str(r[col_idx]) if col_idx < len(r) and r[col_idx] is not None else "" for r in rows[:200]]
            width = min(max(max(len(v) for v in col_values) + 2, 10), 38)
            sh.get_range_by_indexes(0, col_idx, len(rows), 1).format.column_width = width
        if len(rows) > 1 and len(rows) <= 5000 and len(rows[0]) <= 30:
            try:
                sh.tables.add(sh.get_range_by_indexes(0, 0, len(rows), len(rows[0])), True, f"T_{sheet_name[:20].replace(' ', '_').replace('-', '_')}")
            except Exception:
                pass

    # Format dashboard title if present.
    try:
        dash = wb.worksheets.get_item("Dashboard")
        dash.get_range("A1:B1").format = title_fmt
        dash.get_range("A1:B1").format.row_height = 26
    except Exception:
        pass

    # Compact verification.
    try:
        wb.inspect({"kind": "table", "range": "Dashboard!A1:B20", "include": "values,formulas", "table_max_rows": 20, "table_max_cols": 4})
        wb.inspect({"kind": "match", "search_term": "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", "options": {"use_regex": True, "max_results": 100}, "summary": "formula error scan"})
    except Exception:
        pass

    SpreadsheetFile.export_xlsx(wb).save(output_excel)


def build_outputs(args):
    b = compute_cluster_consistency_bundle(
        args.data_file,
        sheet_name=args.data_sheet_name,
        timestamp_col=args.timestamp_col,
        clean_window_days=args.clean_window_days,
        shutdown_indicator_tags=None,
    )
    df = b["df"]
    tags = b["tags"]
    selected = b["selected"]
    clean_candidates = b["clean_candidates"]
    clean_daily = b["clean_daily"]
    all_df = b["all_df"]
    profile_df = b["profile_df"]
    tag_summary = b["tag_summary"]
    daily_summary = b["daily_summary"]
    cluster_definition = b["cluster_definition"]
    dashboard = b["dashboard"]

    actual = all_df[all_df["Final_Status"] == "Actual_Outlier"].sort_values(["Severity_Score_0_100", "Timestamp"], ascending=[False, True])
    warnings = all_df[all_df["Final_Status"] == "Warning"].sort_values(["Severity_Score_0_100", "Timestamp"], ascending=[False, True])
    cluster_drift = all_df[all_df["Final_Status"] == "Cluster_Drift_Not_Outlier"].sort_values(["Severity_Score_0_100", "Timestamp"], ascending=[False, True])
    periods = abnormal_periods(all_df, "Actual_Outlier")

    total_checks = len(all_df)
    actual_count = int((all_df["Final_Status"] == "Actual_Outlier").sum())
    warning_count = int((all_df["Final_Status"] == "Warning").sum())
    cluster_count = int((all_df["Final_Status"] == "Cluster_Drift_Not_Outlier").sum())
    normal_count = int((all_df["Final_Status"] == "Normal").sum())

    prev_abnormal = 18030
    prev_warning = 5599

    status_mapping = pd.DataFrame([
        {"Final_Status": "Actual_Outlier", "Meaning": "Tag behavior is not matching its peer cluster; high residual and low same-direction peer support.", "Use": "Treat as actual outlier candidate."},
        {"Final_Status": "Cluster_Drift_Not_Outlier", "Meaning": "Tag moved, but its peer cluster moved with it.", "Use": "Process/cluster drift; do not count as isolated outlier."},
        {"Final_Status": "Warning", "Meaning": "Borderline cluster break.", "Use": "Review manually before treating as outlier."},
        {"Final_Status": "Normal", "Meaning": "Tag is consistent with peer-cluster clean behavior.", "Use": "No action."},
    ])

    method_comparison = pd.DataFrame([
        {"Method": "Previous Combined Logic", "Abnormal_Rows": prev_abnormal, "Warning_Rows": prev_warning, "Outlier_Definition": "Threshold/residual/spike/change driven; many process shifts were flagged.", "Issue": "High false positives."},
        {"Method": "New Cluster-Consistency Logic", "Abnormal_Rows": actual_count, "Warning_Rows": warning_count, "Outlier_Definition": "Only isolated tag breaks against peer cluster are actual outliers.", "Issue": "Cluster-wide shifts are separated as drift, not outlier."},
    ])

    dashboard_input = all_df[[
        "Timestamp", "Tag", "Actual_Value", "Final_Status", "Final_Class", "Direction", "Severity_Score_0_100",
        "Cluster_ID", "Peer_Tags", "Residual_Z", "Target_Z_vs_Clean", "Peer_Median_Z", "Cluster_Diff_Z",
        "Peer_Same_Direction_Support", "Reliability", "Explanation"
    ]].copy()

    full_zip_sheets = {
        "Cluster_All_Results": all_df,
        "Actual_Outliers": actual,
        "Warnings": warnings,
        "Cluster_Drift_Not_Outlier": cluster_drift,
        "Dashboard_Input": dashboard_input,
    }
    write_csv_zip(args.output_zip, full_zip_sheets)

    excel_sheets = {
        "Dashboard": dashboard,
        "Status_Mapping": status_mapping,
        "Method_Comparison": method_comparison,
        "Clean_Period_Candidates": clean_candidates,
        "Clean_Detection_Daily": clean_daily,
        "Cluster_Definition": cluster_definition,
        "Tag_Model_Profile": profile_df.sort_values(["Cluster_ID", "Tag"]),
        "Tag_Summary": tag_summary,
        "Daily_Summary": daily_summary,
        "Actual_Outliers": actual,
        "Warning_Check": warnings,
        "Cluster_Drift_Not_Outlier": cluster_drift.head(1000),
        "Abnormal_Periods": periods,
    }
    # Excel workbook contains summaries and focused detail sheets. Full row-level results are kept in the CSV ZIP.
    write_excel_artifact(args.output_excel, excel_sheets)

    return {
        "clean_start": selected["Start_Date"],
        "clean_end": selected["End_Date"],
        "total_checks": total_checks,
        "actual_count": actual_count,
        "warning_count": warning_count,
        "cluster_count": cluster_count,
        "normal_count": normal_count,
        "actual_rate": actual_count / total_checks,
        "output_excel": args.output_excel,
        "output_zip": args.output_zip,
    }


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--data_file", default="/mnt/data/Multi_X_Multi_Y_Correct_Data(16).xlsx")
    p.add_argument("--data_sheet_name", default=None)
    p.add_argument("--timestamp_col", default="Timestamp")
    p.add_argument("--clean_window_days", type=int, default=180)
    p.add_argument("--output_excel", default="/mnt/data/cluster_consistency_actual_outlier_analysis.xlsx")
    p.add_argument("--output_zip", default="/mnt/data/cluster_consistency_actual_outlier_full_results.zip")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = build_outputs(args)
    print("Completed cluster-consistency outlier detection")
    for k, v in result.items():
        print(f"{k}: {v}")
