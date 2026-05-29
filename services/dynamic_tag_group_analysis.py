"""
Dynamic tag-group feature selection for peer / ridge models.

Ported from ``dynamic_tag_group_analysis.py`` (repo root): combines Pearson
correlation, mutual information, lag correlation, Random Forest importance, and
agglomerative clustering to pick the best predictor tags per target.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_selection import mutual_info_regression
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler


@dataclass
class DynamicPeerSelection:
    """Selected peer tags and dashboard metadata for one target tag."""

    peer_tags: List[str]
    x_variables: List[Dict[str, Any]]
    cluster_id_by_tag: Dict[str, int]


def _cfg_int(cfg: Dict[str, Any], key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_float(cfg: Dict[str, Any], key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _prepare_target_frame(
    df: pd.DataFrame, target_tag: str, feature_cols: Sequence[str]
) -> Tuple[pd.DataFrame, List[str]]:
    """Numeric frame with median-filled features for analysis."""
    cols = [str(c) for c in feature_cols if str(c) != str(target_tag)]
    if not cols:
        return pd.DataFrame(), []

    sub = df[[target_tag] + cols].apply(pd.to_numeric, errors="coerce")
    sub = sub.dropna(subset=[target_tag])
    if len(sub) < 30:
        return pd.DataFrame(), []

    for col in cols:
        med = sub[col].median()
        sub[col] = sub[col].fillna(med if pd.notna(med) else 0.0)
    sub[target_tag] = sub[target_tag].fillna(sub[target_tag].median())
    return sub, cols


def calculate_correlation(
    frame: pd.DataFrame, target_tag: str, feature_cols: Sequence[str]
) -> pd.DataFrame:
    rows = []
    y = frame[target_tag]
    for col in feature_cols:
        corr = y.corr(frame[col])
        rows.append(
            {
                "Tag": col,
                "Correlation": corr,
                "Abs_Correlation_Score": abs(corr) if pd.notna(corr) else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values("Abs_Correlation_Score", ascending=False)


def calculate_mutual_information(
    frame: pd.DataFrame, target_tag: str, feature_cols: Sequence[str]
) -> pd.DataFrame:
    X = frame[list(feature_cols)].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = frame[target_tag].replace([np.inf, -np.inf], np.nan)
    y = y.fillna(y.median())
    if len(X) < 30:
        return pd.DataFrame({"Tag": list(feature_cols), "Mutual_Information_Score": [0.0] * len(feature_cols)})

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    mi_values = mutual_info_regression(X_scaled, y.to_numpy(), random_state=42)
    return pd.DataFrame(
        {"Tag": list(feature_cols), "Mutual_Information_Score": mi_values}
    ).sort_values("Mutual_Information_Score", ascending=False)


def calculate_lag_correlation(
    frame: pd.DataFrame, target_tag: str, feature_cols: Sequence[str], max_lag: int
) -> pd.DataFrame:
    lag_results = []
    for col in feature_cols:
        best_lag = 0
        best_corr = 0.0
        for lag in range(1, max(1, max_lag) + 1):
            shifted = frame[col].shift(lag)
            temp = pd.DataFrame({"target": frame[target_tag], "feature": shifted}).dropna()
            if len(temp) < 10:
                continue
            corr = temp["target"].corr(temp["feature"])
            if pd.notna(corr) and abs(corr) > abs(best_corr):
                best_corr = float(corr)
                best_lag = lag
        lag_results.append(
            {
                "Tag": col,
                "Best_Lag": best_lag,
                "Lag_Correlation": best_corr,
                "Abs_Lag_Correlation_Score": abs(best_corr),
            }
        )
    return pd.DataFrame(lag_results).sort_values("Abs_Lag_Correlation_Score", ascending=False)


def select_candidate_tags(
    corr_df: pd.DataFrame,
    mi_df: pd.DataFrame,
    lag_df: pd.DataFrame,
    top_n_corr: int,
    top_n_mi: int,
    top_n_lag: int,
) -> List[str]:
    corr_tags = corr_df.head(top_n_corr)["Tag"].astype(str).tolist()
    mi_tags = mi_df.head(top_n_mi)["Tag"].astype(str).tolist()
    lag_tags = lag_df.head(top_n_lag)["Tag"].astype(str).tolist()
    return sorted(set(corr_tags + mi_tags + lag_tags))


def build_rf_importance(
    frame: pd.DataFrame, target_tag: str, candidate_tags: Sequence[str]
) -> pd.DataFrame:
    tags = [str(t) for t in candidate_tags]
    if not tags:
        return pd.DataFrame(columns=["Tag", "Model_Importance"])

    X = frame[tags]
    y = frame[target_tag]
    if len(X) < 40:
        corr = calculate_correlation(frame, target_tag, tags)
        return pd.DataFrame(
            {
                "Tag": corr["Tag"],
                "Model_Importance": corr["Abs_Correlation_Score"].to_numpy(),
            }
        ).sort_values("Model_Importance", ascending=False)

    test_size = 0.2 if len(X) >= 50 else max(0.1, 5.0 / len(X))
    X_train, _, y_train, _ = train_test_split(X, y, test_size=test_size, shuffle=False)

    n_estimators = 120
    model = RandomForestRegressor(
        n_estimators=n_estimators,
        max_depth=8,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    return pd.DataFrame(
        {"Tag": tags, "Model_Importance": model.feature_importances_}
    ).sort_values("Model_Importance", ascending=False)


def create_tag_groups(
    frame: pd.DataFrame, selected_tags: Sequence[str], distance_threshold: float
) -> pd.DataFrame:
    tags = [str(t) for t in selected_tags]
    if len(tags) <= 1:
        return pd.DataFrame({"Tag": tags, "Group_ID": [0] * len(tags)})

    group_corr = frame[tags].corr().fillna(0.0)
    distance_matrix = (1.0 - group_corr.abs()).copy()
    np.fill_diagonal(distance_matrix.values, 0.0)

    try:
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=float(distance_threshold),
            metric="precomputed",
            linkage="average",
        )
    except TypeError:
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=float(distance_threshold),
            affinity="precomputed",
            linkage="average",
        )

    labels = clustering.fit_predict(distance_matrix.to_numpy())
    return pd.DataFrame({"Tag": tags, "Group_ID": labels}).sort_values(["Group_ID", "Tag"])


def select_dynamic_peers_for_target(
    df: pd.DataFrame,
    target_tag: str,
    tag_cols: Sequence[str],
    cfg: Dict[str, Any],
) -> DynamicPeerSelection:
    """
    Pick predictor tags for *target_tag* using multi-criteria feature selection
  + clustering (see repo-root ``dynamic_tag_group_analysis.py``).
    """
    target_tag = str(target_tag)
    frame, feature_cols = _prepare_target_frame(df, target_tag, tag_cols)
    if not feature_cols or frame.empty:
        return DynamicPeerSelection(peer_tags=[], x_variables=[], cluster_id_by_tag={})

    max_peers = _cfg_int(cfg, "max_peers", 5)
    top_n_corr = _cfg_int(cfg, "dynamic_top_n_correlation", 10)
    top_n_mi = _cfg_int(cfg, "dynamic_top_n_mutual_info", 10)
    top_n_lag = _cfg_int(cfg, "dynamic_top_n_lag", 10)
    max_lag = _cfg_int(cfg, "dynamic_max_lag", 5)
    final_top = _cfg_int(cfg, "dynamic_final_top_features", max_peers)
    final_top = min(final_top, max_peers)
    cluster_dist = _cfg_float(cfg, "dynamic_cluster_distance_threshold", 0.5)
    min_abs_corr = _cfg_float(cfg, "min_peer_abs_corr", 0.35)

    if len(frame) > 8000:
        frame = frame.iloc[:: max(1, len(frame) // 8000)].copy()

    corr_df = calculate_correlation(frame, target_tag, feature_cols)
    mi_df = calculate_mutual_information(frame, target_tag, feature_cols)
    lag_df = calculate_lag_correlation(frame, target_tag, feature_cols, max_lag)

    candidates = select_candidate_tags(corr_df, mi_df, lag_df, top_n_corr, top_n_mi, top_n_lag)
    if not candidates:
        return DynamicPeerSelection(peer_tags=[], x_variables=[], cluster_id_by_tag={})

    importance_df = build_rf_importance(frame, target_tag, candidates)
    selected = importance_df.head(final_top)["Tag"].astype(str).tolist()

    corr_map = dict(zip(corr_df["Tag"].astype(str), corr_df["Correlation"]))
    mi_map = dict(zip(mi_df["Tag"].astype(str), mi_df["Mutual_Information_Score"]))
    lag_map = dict(zip(lag_df["Tag"].astype(str), lag_df["Lag_Correlation"]))
    imp_map = dict(zip(importance_df["Tag"].astype(str), importance_df["Model_Importance"]))

    filtered = [
        t
        for t in selected
        if abs(float(corr_map.get(t) or 0.0)) >= min_abs_corr or float(imp_map.get(t) or 0.0) > 0
    ]
    if not filtered:
        filtered = [
            str(r["Tag"])
            for _, r in corr_df.head(max_peers).iterrows()
            if abs(float(r.get("Abs_Correlation_Score") or 0)) >= min_abs_corr
        ]
    selected = filtered[:final_top] if filtered else selected[:final_top]

    group_df = create_tag_groups(frame, selected, cluster_dist)
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
            }
        )

    return DynamicPeerSelection(
        peer_tags=selected,
        x_variables=x_variables,
        cluster_id_by_tag=cluster_map,
    )


def build_dynamic_peer_models(
    df: pd.DataFrame,
    tag_cols: Sequence[str],
    cfg: Dict[str, Any],
    *,
    fallback_peers_fn: Optional[Any] = None,
) -> Tuple[Dict[str, List[Tuple[str, float]]], Dict[str, List[Dict[str, Any]]]]:
    """
    Build ``peers_by_tag`` and ``x_variables_by_tag`` for all targets.

    *fallback_peers_fn* — optional ``(df, tag, tag_cols, cfg) -> List[Tuple[str, float]]``
    used when dynamic selection returns no peers (typically Pearson top-k).
    """
    use_dynamic = bool(cfg.get("use_dynamic_peer_selection", True))
    peers_by_tag: Dict[str, List[Tuple[str, float]]] = {}
    x_variables_by_tag: Dict[str, List[Dict[str, Any]]] = {}

    for tag in tag_cols:
        tag_s = str(tag)
        selection = DynamicPeerSelection([], [], {})
        if use_dynamic:
            try:
                selection = select_dynamic_peers_for_target(df, tag_s, tag_cols, cfg)
            except Exception:
                selection = DynamicPeerSelection([], [], {})

        if selection.peer_tags:
            peers_by_tag[tag_s] = [
                (t, float(next((x["corr"] for x in selection.x_variables if x.get("tag") == t), 0.0)))
                for t in selection.peer_tags
            ]
            x_variables_by_tag[tag_s] = list(selection.x_variables)
            continue

        if fallback_peers_fn is not None:
            fallback = fallback_peers_fn(df, tag_s, tag_cols, cfg)
            peers_by_tag[tag_s] = fallback
            x_variables_by_tag[tag_s] = [
                {"tag": p, "corr": float(c), "group_id": 0, "model_importance": abs(float(c))}
                for p, c in fallback
            ]
        else:
            peers_by_tag[tag_s] = []
            x_variables_by_tag[tag_s] = []

    return peers_by_tag, x_variables_by_tag
