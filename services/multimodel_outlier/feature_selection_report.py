"""Per-feature stats for Models & Clusters UI (Panel A)."""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Set

import numpy as np
import pandas as pd
from sklearn.feature_selection import f_regression, mutual_info_regression

from services.numeric_safe import safe_series_corr
from sklearn.linear_model import ElasticNetCV, RidgeCV


def display_feature_name(target_tag: str, feature: str) -> str:
    tag = str(target_tag).strip()
    f = str(feature).strip()
    if f.startswith(f"{tag}__"):
        return f
    if f.startswith("peer_delta__"):
        peer = f.replace("peer_delta__", "", 1)
        return f"{tag}__peer_delta__{peer}"
    return f"{tag}__{f}"


def compute_relevance_stats(
    X: pd.DataFrame, y: pd.Series, cols: Sequence[str], cfg: Dict[str, Any]
) -> Dict[str, Dict[str, float]]:
    sub = X[list(cols)].copy()
    yy = pd.to_numeric(y, errors="coerce")
    mask = yy.notna() & sub.notna().all(axis=1)
    out: Dict[str, Dict[str, float]] = {}
    if mask.sum() < 20:
        for c in cols:
            out[str(c)] = {"pearson_r": 0.0, "spearman_r": 0.0, "mi": 0.0, "f_stat": 0.0}
        return out
    sub = sub.loc[mask].fillna(0.0)
    yy = yy.loc[mask]
    col_list = [str(c) for c in cols]
    for c in col_list:
        pv = safe_series_corr(sub[c], yy, method="pearson")
        sv = safe_series_corr(sub[c], yy, method="spearman")
        out[c] = {
            "pearson_r": abs(float(pv)) if pd.notna(pv) else 0.0,
            "spearman_r": abs(float(sv)) if pd.notna(sv) else 0.0,
            "mi": 0.0,
            "f_stat": 0.0,
        }
    try:
        mi = mutual_info_regression(
            sub.values, yy.values, random_state=int(cfg.get("random_state") or 42)
        )
        for i, c in enumerate(col_list):
            out[c]["mi"] = float(mi[i])
    except Exception:
        pass
    try:
        fvals, _ = f_regression(sub.values, yy.values)
        for i, c in enumerate(col_list):
            fv = float(fvals[i]) if np.isfinite(fvals[i]) else 0.0
            out[c]["f_stat"] = fv
    except Exception:
        pass
    return out


def compute_selector_votes(
    X: pd.DataFrame, y: pd.Series, cols: Sequence[str], cfg: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    sub = X[list(cols)].copy()
    yy = pd.to_numeric(y, errors="coerce")
    mask = yy.notna() & sub.notna().all(axis=1)
    col_list = [str(c) for c in cols]
    detail = {
        c: {"en": False, "ridge": False, "rfe": False, "votes": 0} for c in col_list
    }
    if mask.sum() < 50:
        return detail
    sub = sub.loc[mask].fillna(0.0)
    yy = yy.loc[mask]
    xs = np.asarray(sub.values, dtype=float).copy()
    ys = np.asarray(yy.values, dtype=float).copy()

    try:
        enet = ElasticNetCV(cv=3, random_state=int(cfg.get("random_state") or 42), max_iter=2000)
        enet.fit(xs, ys)
        for i, c in enumerate(col_list):
            if abs(float(enet.coef_[i])) > 1e-8:
                detail[c]["en"] = True
                detail[c]["votes"] += 1
    except Exception:
        pass

    try:
        ridge = RidgeCV(alphas=np.logspace(-3, 2, 12))
        ridge.fit(xs, ys)
        for i, c in enumerate(col_list):
            if abs(float(ridge.coef_[i])) > 1e-8:
                detail[c]["ridge"] = True
                detail[c]["votes"] += 1
    except Exception:
        pass

    if cfg.get("stage5_use_rfe", True) and len(col_list) > 3:
        try:
            from sklearn.feature_selection import RFE
            from sklearn.linear_model import Ridge

            est = Ridge(alpha=1.0)
            n_keep = max(3, len(col_list) // 2)
            rfe = RFE(est, n_features_to_select=n_keep)
            rfe.fit(xs, ys)
            for i, c in enumerate(col_list):
                if rfe.support_[i]:
                    detail[c]["rfe"] = True
                    detail[c]["votes"] += 1
        except Exception:
            pass
    return detail


def compute_stability_scores(
    X: pd.DataFrame, y: pd.Series, cols: Sequence[str], cfg: Dict[str, Any]
) -> Dict[str, float]:
    n_boot = int(cfg.get("stage6_bootstrap_n") or 5)
    col_list = [str(c) for c in cols]
    counts: Dict[str, int] = {c: 0 for c in col_list}
    yy = pd.to_numeric(y, errors="coerce")
    sub_all = X[col_list].copy()
    mask = yy.notna() & sub_all.notna().all(axis=1)
    if mask.sum() < 60:
        return {c: 0.0 for c in col_list}
    sub_all = sub_all.loc[mask].fillna(0.0)
    yy = yy.loc[mask]
    # Cap bootstrap data size for speed.
    max_sel = int(cfg.get("max_selection_sample") or 1500)
    n = len(sub_all)
    rng = np.random.default_rng(int(cfg.get("random_state") or 42))
    if n > max_sel:
        cap_idx = np.sort(rng.choice(n, size=max_sel, replace=False))
        sub_all = sub_all.iloc[cap_idx]
        yy = yy.iloc[cap_idx]
        n = max_sel
    for _ in range(n_boot):
        idx = rng.choice(n, size=int(n * 0.8), replace=True)
        sub = sub_all.iloc[idx]
        yb = yy.iloc[idx]
        try:
            enet = ElasticNetCV(cv=3, random_state=42, max_iter=800)
            xs = np.asarray(sub.values, dtype=float).copy()
            yv = np.asarray(yb.values, dtype=float).copy()
            enet.fit(xs, yv)
            for i, c in enumerate(col_list):
                if abs(float(enet.coef_[i])) > 1e-8:
                    counts[c] += 1
        except Exception:
            continue
    return {c: round(counts[c] / max(1, n_boot), 4) for c in col_list}


def _feature_status(
    feat: str,
    *,
    in_s2: bool,
    in_s3: bool,
    in_s6: bool,
    in_model: bool,
) -> str:
    if in_model and in_s6:
        return "Selected (final)"
    if in_s2 and not in_s3:
        return "Dropped (collinear)"
    return "Dropped"


def build_cluster_methodology_report(
    target_tag: str,
    trail: Dict[str, Any],
    model_features: Sequence[str],
) -> List[Dict[str, Any]]:
    """Rows for Models & Clusters — cluster methodology peer tags (Panel A)."""
    model_set = {str(f) for f in model_features}
    x_vars = trail.get("x_variables") or []
    by_tag = {str(x.get("tag")): x for x in x_vars if x.get("tag")}
    candidates = [str(t) for t in (trail.get("candidate_tags") or [])]
    if not candidates:
        candidates = list(by_tag.keys())

    rows: List[Dict[str, Any]] = []
    for tag in candidates:
        meta = by_tag.get(tag, {})
        in_model = tag in model_set
        rows.append(
            {
                "feature": str(tag),
                "feature_key": str(tag),
                "status": "Selected (final)" if in_model else "Dropped",
                "pearson_r": round(abs(float(meta.get("corr") or 0.0)), 4),
                "spearman_r": round(abs(float(meta.get("corr") or 0.0)), 4),
                "mi": round(float(meta.get("mutual_information") or 0.0), 4),
                "f_stat": round(float(meta.get("model_importance") or 0.0), 4),
                "en_vote": False,
                "ridge_vote": False,
                "rfe_vote": False,
                "votes": int(meta.get("group_id") or 0),
                "stability": round(float(meta.get("lag_correlation") or 0.0), 4),
                "in_model": in_model,
                "cluster_id": int(meta.get("group_id") or 0),
            }
        )

    for tag in model_set:
        if tag not in candidates:
            meta = by_tag.get(tag, {})
            rows.append(
                {
                    "feature": str(tag),
                    "feature_key": str(tag),
                    "status": "Selected (final)",
                    "pearson_r": round(abs(float(meta.get("corr") or 0.0)), 4),
                    "spearman_r": round(abs(float(meta.get("corr") or 0.0)), 4),
                    "mi": round(float(meta.get("mutual_information") or 0.0), 4),
                    "f_stat": round(float(meta.get("model_importance") or 0.0), 4),
                    "en_vote": False,
                    "ridge_vote": False,
                    "rfe_vote": False,
                    "votes": int(meta.get("group_id") or 0),
                    "stability": round(float(meta.get("lag_correlation") or 0.0), 4),
                    "in_model": True,
                    "cluster_id": int(meta.get("group_id") or 0),
                }
            )

    def _sort_key(r: Dict[str, Any]) -> tuple:
        selected = 0 if r.get("in_model") else 1
        return (selected, -float(r.get("f_stat") or 0), -float(r.get("pearson_r") or 0))

    rows.sort(key=_sort_key)
    return rows


def build_feature_selection_report(
    target_tag: str,
    X1: pd.DataFrame,
    y: pd.Series,
    stage_sets: Dict[str, List[str]],
    model_features: Sequence[str],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Rows for Panel A — legacy engineered-feature pipeline stats."""
    all_cols = [str(c) for c in X1.columns]
    s2: Set[str] = set(stage_sets.get("s2") or [])
    s3: Set[str] = set(stage_sets.get("s3") or [])
    s6: Set[str] = set(stage_sets.get("s6") or [])
    model_set: Set[str] = {str(f) for f in model_features}
    vote_cols = stage_sets.get("s4") or stage_sets.get("s3") or all_cols
    relevance = compute_relevance_stats(X1, y, all_cols, cfg)
    votes = compute_selector_votes(X1, y, vote_cols, cfg)
    stability = compute_stability_scores(X1, y, s6 or vote_cols, cfg)

    rows: List[Dict[str, Any]] = []
    for feat in all_cols:
        rel = relevance.get(feat, {})
        vd = votes.get(feat, {})
        in_model = feat in model_set
        status = _feature_status(
            feat,
            in_s2=feat in s2,
            in_s3=feat in s3,
            in_s6=feat in s6,
            in_model=in_model,
        )
        rows.append(
            {
                "feature": display_feature_name(target_tag, feat),
                "feature_key": feat,
                "status": status,
                "pearson_r": round(float(rel.get("pearson_r") or 0.0), 4),
                "spearman_r": round(float(rel.get("spearman_r") or 0.0), 4),
                "mi": round(float(rel.get("mi") or 0.0), 4),
                "f_stat": float(rel.get("f_stat") or 0.0),
                "en_vote": bool(vd.get("en")),
                "ridge_vote": bool(vd.get("ridge")),
                "rfe_vote": bool(vd.get("rfe")),
                "votes": int(vd.get("votes") or 0),
                "stability": float(stability.get(feat, 0.0)),
                "in_model": in_model,
            }
        )

    def _sort_key(r: Dict[str, Any]) -> tuple:
        selected = 0 if r["status"] == "Selected (final)" else 1
        return (selected, -float(r.get("votes") or 0), -float(r.get("spearman_r") or 0))

    rows.sort(key=_sort_key)
    return rows
