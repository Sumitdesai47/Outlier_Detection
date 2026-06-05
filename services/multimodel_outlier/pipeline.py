"""Build multimodel S5 predictions per tag for consensus workflow."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from services.multimodel_outlier.config import DEFAULT_CFG
from services.numeric_safe import safe_corr_matrix
from services.multimodel_outlier.cluster_feature_selection import run_cluster_feature_selection
from services.multimodel_outlier.feature_selection_report import build_cluster_methodology_report
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
    corr = safe_corr_matrix(sub)
    dist = 1.0 - corr.abs()
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


def _x_variables_from_trail(
    features: List[str],
    trail: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Use cluster methodology x_variables; fill gaps for any model feature."""
    by_tag = {
        str(x.get("tag")): x
        for x in (trail.get("x_variables") or [])
        if x.get("tag")
    }
    cluster_map = trail.get("cluster_id_by_tag") or {}
    rows: List[Dict[str, Any]] = []
    for i, f in enumerate(features[:12]):
        f = str(f)
        if f in by_tag:
            rows.append(dict(by_tag[f]))
            continue
        corr = float((by_tag.get(f) or {}).get("corr") or 0.0)
        rows.append(
            {
                "tag": f,
                "corr": corr,
                "abs_corr": abs(corr),
                "group_id": int(cluster_map.get(f, 0)),
                "model_importance": 1.0 / (i + 1),
                "feature_name": f,
            }
        )
    return rows


def _safe_float(v: Any) -> Any:
    """Convert numpy/inf/nan to a JSON-safe Python float or None."""
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    import math
    if not math.isfinite(f):
        return None
    return round(f, 6)


def _safe_candidates(cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure every candidate row has only JSON-safe values."""
    out = []
    for c in (cands or []):
        out.append({
            "model_name": str(c.get("model_name") or ""),
            "model_family": str(c.get("model_family") or ""),
            "cv_rmse": _safe_float(c.get("cv_rmse")),
            "cv_r2": _safe_float(c.get("cv_r2")),
            "status": str(c.get("status") or ""),
            "is_winner": bool(c.get("is_winner")),
        })
    return out


def multimodel_meta_for_ui(mm: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-serializable slice for results page (no pandas / numpy objects)."""
    # Shared base keeps all keys present regardless of error path.
    base: Dict[str, Any] = {
        "error": None,
        "model_type": None,
        "winner_model": None,
        "winner_cv_rmse": None,
        "winner_cv_r2": None,
        "selection_reason": None,
        "model_candidates": [],
        "feature_trail": {},
        "features_final": [],
        "feature_clusters": [],
        "x_variables": [],
        "feature_selection": [],
        "n_features_in_model": 0,
    }
    if mm.get("error"):
        base.update({
            "error": str(mm.get("error")),
            "selection_reason": str(mm.get("selection_reason") or "Model training failed."),
        })
        return base
    features_final = list(mm.get("features_final") or [])
    base.update({
        "model_type": mm.get("model_type"),
        "winner_model": mm.get("model_name"),
        "winner_cv_rmse": _safe_float(mm.get("cv_rmse")),
        "winner_cv_r2": _safe_float(mm.get("cv_r2")),
        "selection_reason": mm.get("selection_reason"),
        "model_candidates": _safe_candidates(mm.get("model_candidates") or []),
        "feature_trail": mm.get("feature_trail") or {},
        "features_final": features_final,
        "feature_clusters": mm.get("feature_clusters") or [],
        "x_variables": mm.get("x_variables") or [],
        "feature_selection": mm.get("feature_selection") or [],
        "n_features_in_model": len(features_final),
    })
    return base


def build_s5_for_tag(
    df: pd.DataFrame,
    target_tag: str,
    tag_cols: Sequence[str],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Train multimodel on cluster-selected peer tags; return S5 series + metadata."""
    X_all, x_final, trail = run_cluster_feature_selection(df, target_tag, tag_cols, cfg)
    if not x_final:
        from services.robust_consensus_outlier_workflow import _pick_peers

        peers = _pick_peers(df, str(target_tag), list(tag_cols), cfg)
        if not peers:
            raise ValueError(trail.get("selection_note") or "No peer tags selected for this target.")
        x_final = [p for p, _ in peers]
        X_all = pd.DataFrame(
            {t: pd.to_numeric(df[t], errors="coerce") for t in x_final},
            index=df.index,
        )
        trail["selected_tags"] = x_final
        trail["selection_note"] = (
            "Cluster filters returned no peers; using top Pearson-correlated tags as fallback."
        )
        trail["x_variables"] = [
            {"tag": p, "corr": float(c), "abs_corr": abs(float(c)), "group_id": 0, "feature_name": p}
            for p, c in peers
        ]
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

    cluster_map = trail.get("cluster_id_by_tag") or {}
    clusters = [
        {"feature": str(f), "cluster_id": int(cluster_map.get(str(f), 0))}
        for f in bundle.features
    ]
    if not clusters:
        clusters = build_feature_clusters(X_all, bundle.features)
    x_vars = _x_variables_from_trail(bundle.features, trail)
    feature_selection = build_cluster_methodology_report(
        target_tag,
        trail,
        bundle.features,
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


def _worker(
    tag: str,
    df: pd.DataFrame,
    tag_cols: Sequence[str],
    cfg: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    """Single-tag worker for ThreadPoolExecutor."""
    try:
        return tag, build_s5_for_tag(df, tag, tag_cols, cfg)
    except Exception as exc:
        return tag, {
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


def _resolve_workers(cfg: Dict[str, Any]) -> int:
    """Determine number of parallel threads: env → cfg → auto (half CPUs, min 2)."""
    env = os.environ.get("MM_PARALLEL_WORKERS")
    if env is not None and env.strip():
        return max(1, int(env.strip()))
    n = int(cfg.get("n_parallel_workers") or 0)
    if n > 0:
        return n
    cpu = os.cpu_count() or 2
    return max(2, cpu // 2)


def build_multimodel_s5_by_tag(
    df: pd.DataFrame,
    tag_cols: Sequence[str],
    critical_tags: Optional[Sequence[str]],
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Train multimodel S5 per tag in parallel (ThreadPoolExecutor)."""
    local = dict(DEFAULT_CFG)
    if cfg:
        local.update(cfg)
    if critical_tags is not None:
        targets = [str(t) for t in critical_tags if str(t) in tag_cols]
    else:
        targets = [str(t) for t in tag_cols]

    if not targets:
        return {}

    n_workers = min(_resolve_workers(local), len(targets))
    out: Dict[str, Dict[str, Any]] = {}

    if n_workers <= 1 or len(targets) == 1:
        # Sequential fallback (avoids threading overhead for tiny workloads).
        for tag in targets:
            tag_out, result = _worker(tag, df, tag_cols, local)
            out[tag_out] = result
    else:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {
                pool.submit(_worker, tag, df, tag_cols, local): tag
                for tag in targets
            }
            for fut in as_completed(futures):
                tag_out, result = fut.result()
                out[tag_out] = result

    return out
