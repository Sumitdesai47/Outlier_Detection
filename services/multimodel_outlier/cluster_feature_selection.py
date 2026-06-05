"""
Cluster methodology feature selection for multimodel S5 (per target tag).

Follows ``docs/Cluster_Methodology_Document.docx`` / ``dynamic_tag_group_analysis``:
Pearson correlation, mutual information, lag correlation, Random Forest importance,
agglomerative clustering on |correlation| distance, then raw peer tag values as X.
No engineered rolling / delta features.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import pandas as pd

from services.dynamic_tag_group_analysis import (
    _cfg_float,
    _cfg_int,
    _prepare_target_frame,
    build_rf_importance,
    calculate_correlation,
    calculate_lag_correlation,
    calculate_mutual_information,
    create_tag_groups,
    select_candidate_tags,
    select_dynamic_peers_for_target,
)


def _peer_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Map multimodel cfg keys to dynamic peer selection."""
    max_feat = int(cfg.get("max_features") or 10)
    out = dict(cfg)
    out.setdefault("use_dynamic_peer_selection", True)
    out.setdefault("max_peers", max_feat)
    out.setdefault("dynamic_final_top_features", max_feat)
    out.setdefault("dynamic_top_n_correlation", int(cfg.get("dynamic_top_n_correlation") or 10))
    out.setdefault("dynamic_top_n_mutual_info", int(cfg.get("dynamic_top_n_mutual_info") or 10))
    out.setdefault("dynamic_top_n_lag", int(cfg.get("dynamic_top_n_lag") or 10))
    out.setdefault("dynamic_max_lag", int(cfg.get("dynamic_max_lag") or 5))
    out.setdefault("dynamic_cluster_distance_threshold", float(
        cfg.get("dynamic_cluster_distance_threshold") or 0.5
    ))
    out.setdefault("min_peer_abs_corr", float(cfg.get("min_peer_abs_corr") or 0.15))
    return out


def run_cluster_feature_selection(
    df: pd.DataFrame,
    target_tag: str,
    tag_cols: Sequence[str],
    cfg: Dict[str, Any],
) -> Tuple[pd.DataFrame, List[str], Dict[str, Any]]:
    """
    Select X features (peer process tags) via cluster methodology; return raw-tag matrix.

    Returns (X, feature_names, trail_metadata).
    """
    target_tag = str(target_tag)
    peer_cfg = _peer_cfg(cfg)
    max_feat = int(peer_cfg.get("max_features") or 10)

    frame, feature_cols = _prepare_target_frame(df, target_tag, tag_cols)
    trail: Dict[str, Any] = {
        "methodology": "cluster",
        "target_tag": target_tag,
        "candidate_tags": [],
        "selected_tags": [],
        "cluster_id_by_tag": {},
        "x_variables": [],
    }

    if not feature_cols or frame.empty:
        trail["selection_note"] = "Insufficient data for cluster feature selection."
        return pd.DataFrame(index=df.index), [], trail

    top_n_corr = _cfg_int(peer_cfg, "dynamic_top_n_correlation", 10)
    top_n_mi = _cfg_int(peer_cfg, "dynamic_top_n_mutual_info", 10)
    top_n_lag = _cfg_int(peer_cfg, "dynamic_top_n_lag", 10)
    max_lag = _cfg_int(peer_cfg, "dynamic_max_lag", 5)
    cluster_dist = _cfg_float(peer_cfg, "dynamic_cluster_distance_threshold", 0.5)
    min_abs_corr = _cfg_float(peer_cfg, "min_peer_abs_corr", 0.35)

    work = frame
    if len(work) > 8000:
        work = work.iloc[:: max(1, len(work) // 8000)].copy()

    corr_df = calculate_correlation(work, target_tag, feature_cols)
    mi_df = calculate_mutual_information(work, target_tag, feature_cols)
    lag_df = calculate_lag_correlation(work, target_tag, feature_cols, max_lag)
    candidates = select_candidate_tags(corr_df, mi_df, lag_df, top_n_corr, top_n_mi, top_n_lag)
    trail["candidate_tags"] = list(candidates)

    if not candidates:
        selection = select_dynamic_peers_for_target(df, target_tag, tag_cols, peer_cfg)
        selected = list(selection.peer_tags)[:max_feat]
        trail["cluster_id_by_tag"] = dict(selection.cluster_id_by_tag)
        trail["x_variables"] = list(selection.x_variables)
    else:
        importance_df = build_rf_importance(work, target_tag, candidates)
        selected = importance_df.head(max_feat)["Tag"].astype(str).tolist()

        corr_map = dict(zip(corr_df["Tag"].astype(str), corr_df["Correlation"]))
        mi_map = dict(zip(mi_df["Tag"].astype(str), mi_df["Mutual_Information_Score"]))
        lag_map = dict(zip(lag_df["Tag"].astype(str), lag_df["Lag_Correlation"]))
        imp_map = dict(zip(importance_df["Tag"].astype(str), importance_df["Model_Importance"]))

        filtered = [
            t
            for t in selected
            if abs(float(corr_map.get(t) or 0.0)) >= min_abs_corr
            or float(imp_map.get(t) or 0.0) > 0
        ]
        if not filtered:
            filtered = corr_df.head(max_feat)["Tag"].astype(str).tolist()
        selected = (filtered or selected or candidates)[:max_feat]

        group_df = create_tag_groups(work, selected, cluster_dist)
        cluster_map = dict(zip(group_df["Tag"].astype(str), group_df["Group_ID"].astype(int)))

        x_variables: List[Dict[str, Any]] = []
        for tag in selected:
            corr_val = float(corr_map.get(tag) or 0.0)
            x_variables.append(
                {
                    "tag": tag,
                    "corr": corr_val,
                    "abs_corr": abs(corr_val),
                    "mutual_information": float(mi_map.get(tag) or 0.0),
                    "lag_correlation": float(lag_map.get(tag) or 0.0),
                    "model_importance": float(imp_map.get(tag) or 0.0),
                    "group_id": int(cluster_map.get(tag, 0)),
                    "feature_name": tag,
                }
            )
        trail["cluster_id_by_tag"] = cluster_map
        trail["x_variables"] = x_variables
        trail["importance_rank"] = importance_df.to_dict(orient="records")

    trail["selected_tags"] = selected

    if not selected:
        trail["selection_note"] = "No peer tags passed cluster methodology filters."
        return pd.DataFrame(index=df.index), [], trail

    X = pd.DataFrame(
        {t: pd.to_numeric(df[t], errors="coerce") for t in selected},
        index=df.index,
    )
    trail["selection_note"] = (
        f"Cluster methodology: {len(candidates)} candidates → {len(selected)} tags "
        f"(Pearson + MI + lag + RF importance + agglomerative clusters)."
    )
    trail["stage_sets"] = {
        "candidates": list(candidates),
        "selected": list(selected),
    }
    return X, selected, trail
