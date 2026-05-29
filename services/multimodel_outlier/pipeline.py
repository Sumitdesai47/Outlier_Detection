"""Build multimodel S5 predictions per tag for consensus workflow."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from services.multimodel_outlier.config import DEFAULT_CFG
from services.multimodel_outlier.feature_selection_report import build_feature_selection_report
from services.multimodel_outlier.feature_stages import run_feature_stages
from services.multimodel_outlier.model_training import ModelBundle, train_winner
from services.robust_consensus_outlier_workflow import _mad_scale


def build_feature_clusters(
    X: pd.DataFrame,
    features: List[str],
    *,
    distance_threshold: float = 0.5,
) -> List[Dict[str, Any]]:
    """Agglomerative clusters on final model features (correlation distance)."""
    feats = [f for f in features if f in X.columns]
    if not feats:
        return []
    if len(feats) == 1:
        return [{"feature": feats[0], "cluster_id": 0}]

    sub = X[feats].apply(pd.to_numeric, errors="coerce")
    for c in sub.columns:
        sub[c] = sub[c].fillna(sub[c].median())
    corr = sub.corr().fillna(0.0)
    dist = (1.0 - corr.abs()).copy()
    dist_arr = np.asarray(dist, dtype=float).copy()
    np.fill_diagonal(dist_arr, 0.0)

    try:
        from sklearn.cluster import AgglomerativeClustering

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
        labels = clustering.fit_predict(dist_arr)
    except Exception:
        labels = list(range(len(feats)))

    return [
        {"feature": str(f), "cluster_id": int(labels[i])}
        for i, f in enumerate(feats)
    ]


def _features_to_x_variables(
    features: List[str],
    trail: Dict[str, Any],
    bundle: ModelBundle,
    clusters: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    cluster_map = {str(c["feature"]): int(c["cluster_id"]) for c in clusters}
    rows: List[Dict[str, Any]] = []
    for i, f in enumerate(features[:12]):
        cid = int(cluster_map.get(f, 0))
        if f.startswith("peer_delta__"):
            tag = f.replace("peer_delta__", "", 1)
            rows.append(
                {
                    "tag": tag,
                    "corr": 0.0,
                    "abs_corr": 0.0,
                    "group_id": cid,
                    "model_importance": 1.0 / (i + 1),
                    "feature_name": f,
                }
            )
        else:
            rows.append(
                {
                    "tag": f,
                    "corr": 0.0,
                    "abs_corr": 0.0,
                    "group_id": cid,
                    "model_importance": 1.0 / (i + 1),
                    "feature_name": f,
                }
            )
    return rows


def multimodel_meta_for_ui(mm: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-serializable slice for results page (no pandas objects)."""
    if mm.get("error"):
        return {
            "error": str(mm.get("error")),
            "model_type": None,
            "winner_model": None,
            "winner_cv_rmse": None,
            "selection_reason": mm.get("selection_reason") or "Model training failed.",
            "model_candidates": [],
            "feature_trail": {},
            "features_final": [],
            "feature_clusters": [],
            "x_variables": [],
            "feature_selection": [],
        }
    return {
        "model_type": mm.get("model_type"),
        "winner_model": mm.get("model_name"),
        "winner_cv_rmse": mm.get("cv_rmse"),
        "winner_cv_r2": mm.get("cv_r2"),
        "selection_reason": mm.get("selection_reason"),
        "model_candidates": mm.get("model_candidates") or [],
        "feature_trail": mm.get("feature_trail") or {},
        "features_final": mm.get("features_final") or [],
        "feature_clusters": mm.get("feature_clusters") or [],
        "x_variables": mm.get("x_variables") or [],
        "feature_selection": mm.get("feature_selection") or [],
        "n_features_in_model": len(mm.get("features_final") or []),
    }


def build_s5_for_tag(
    df: pd.DataFrame,
    target_tag: str,
    tag_cols: Sequence[str],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Train multimodel on engineered features; return S5 series + metadata."""
    X_all, x_final, trail = run_feature_stages(df, target_tag, tag_cols, cfg)
    y = pd.to_numeric(df[target_tag], errors="coerce")
    bundle = train_winner(X_all, y, x_final, cfg)

    sub = X_all[bundle.features].copy()
    for c in sub.columns:
        sub[c] = sub[c].fillna(sub[c].median())
    Xs = bundle.scaler.transform(np.asarray(sub.values, dtype=float).copy())
    pred = np.asarray(bundle.model.predict(Xs), dtype=float).reshape(-1)
    predicted = pd.Series(pred, index=df.index)
    yy = np.asarray(pd.to_numeric(y, errors="coerce"), dtype=float).reshape(-1)
    resid = yy - pred
    mask = np.isfinite(resid) & y.notna().to_numpy()
    resid_scale = _mad_scale(pd.Series(resid[mask]), min_mad=float(cfg.get("min_mad") or 1e-6))
    z_peer = pd.Series(np.nan, index=df.index)
    if np.isfinite(resid_scale) and resid_scale > float(cfg.get("min_mad") or 1e-6):
        z_peer = pd.Series(resid / resid_scale, index=df.index)

    clusters = build_feature_clusters(X_all, bundle.features)
    x_vars = _features_to_x_variables(bundle.features, trail, bundle, clusters)
    stage_sets = (trail.get("stage_sets") or {}) if isinstance(trail, dict) else {}
    feature_selection = build_feature_selection_report(
        target_tag,
        X_all,
        y,
        stage_sets,
        bundle.features,
        cfg,
    )
    return {
        "predicted": predicted,
        "z_peer": z_peer,
        "x_variables": x_vars,
        "model_name": bundle.model_name,
        "model_type": bundle.model_type,
        "cv_rmse": bundle.cv_rmse,
        "cv_r2": bundle.cv_r2,
        "features_final": list(bundle.features),
        "feature_trail": trail,
        "feature_clusters": clusters,
        "model_candidates": list(bundle.candidates),
        "selection_reason": bundle.selection_reason,
        "feature_selection": feature_selection,
    }


def build_multimodel_s5_by_tag(
    df: pd.DataFrame,
    tag_cols: Sequence[str],
    critical_tags: Optional[Sequence[str]],
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Run multimodel pipeline per tag in ``critical_tags`` (target list), or all ``tag_cols`` when None."""
    local = dict(DEFAULT_CFG)
    if cfg:
        local.update(cfg)
    if critical_tags is not None:
        targets = [str(t) for t in critical_tags if str(t) in tag_cols]
    else:
        targets = [str(t) for t in tag_cols]

    out: Dict[str, Dict[str, Any]] = {}
    for tag in targets:
        try:
            out[tag] = build_s5_for_tag(df, tag, tag_cols, local)
        except Exception as exc:
            out[str(tag)] = {
                "error": str(exc) or type(exc).__name__,
                "model_name": None,
                "model_type": None,
                "cv_rmse": None,
                "model_candidates": [],
                "feature_trail": {},
                "features_final": [],
                "feature_clusters": [],
                "selection_reason": "Training failed for this tag.",
            }
    return out
