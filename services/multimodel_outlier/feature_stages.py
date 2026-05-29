"""Stages 1–6: engineered features and sequential selection."""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.feature_selection import f_regression, mutual_info_regression
from sklearn.linear_model import ElasticNetCV, RidgeCV
from sklearn.preprocessing import StandardScaler


def _numeric_frame(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    out = df[list(cols)].apply(pd.to_numeric, errors="coerce")
    return out.replace([np.inf, -np.inf], np.nan)


def _subsample(
    sub: pd.DataFrame,
    yy: pd.Series,
    max_n: int,
    seed: int = 42,
) -> Tuple[pd.DataFrame, pd.Series]:
    """Random subsample when n > max_n. Keeps indices aligned."""
    n = len(sub)
    if n <= max_n:
        return sub, yy
    idx = np.random.default_rng(seed).choice(n, size=max_n, replace=False)
    idx = np.sort(idx)
    return sub.iloc[idx], yy.iloc[idx]


def stage1_raw_features(
    df: pd.DataFrame,
    target_tag: str,
    tag_cols: Sequence[str],
    cfg: Dict[str, Any],
) -> pd.DataFrame:
    """Build wide engineered feature matrix aligned to df index."""
    ts_col = "Timestamp" if "Timestamp" in df.columns else None
    y = pd.to_numeric(df[target_tag], errors="coerce")
    feats: Dict[str, pd.Series] = {"raw_target": y}

    for w in cfg.get("rolling_windows") or [9, 31]:
        r = y.rolling(w, min_periods=max(3, w // 3))
        feats[f"roll_mean_{w}"] = r.mean()
        feats[f"roll_std_{w}"] = r.std()
        feats[f"roll_min_{w}"] = r.min()
        feats[f"roll_max_{w}"] = r.max()
    feats["diff1"] = y.diff()
    feats["resid_roll31"] = y - y.rolling(31, min_periods=10).mean()
    feats["baseline_resid"] = y - y.expanding(min_periods=10).median()
    feats["diff_abs"] = y.diff().abs()

    peers = [c for c in tag_cols if str(c) != str(target_tag)]
    max_peers = int(cfg.get("max_peer_features") or 5)
    if peers:
        peers_df = _numeric_frame(df, peers)
        corr = peers_df.corrwith(y, axis=0).dropna().abs().sort_values(ascending=False)
        for peer in corr.head(max_peers).index.astype(str):
            p = pd.to_numeric(df[peer], errors="coerce")
            feats[f"peer_delta__{peer}"] = y - p

    if ts_col:
        ts = pd.to_datetime(df[ts_col], errors="coerce")
        feats["hour"] = ts.dt.hour.astype(float)
        feats["dow"] = ts.dt.dayofweek.astype(float)

    frac = float(cfg.get("early_segment_fraction") or 0.28)
    n_early = max(10, int(len(df) * frac))
    early = pd.Series(0.0, index=df.index)
    early.iloc[:n_early] = 1.0
    feats["early_segment_flag"] = early

    X = pd.DataFrame(feats, index=df.index)
    return X


def stage2_variance(X: pd.DataFrame, cfg: Dict[str, Any]) -> List[str]:
    keep: List[str] = []
    min_var = float(cfg.get("stage2_min_variance") or 1e-8)
    max_miss = float(cfg.get("stage2_max_missing_frac") or 0.35)
    for c in X.columns:
        s = X[c]
        if s.notna().mean() < (1.0 - max_miss):
            continue
        if s.var(skipna=True) >= min_var:
            keep.append(str(c))
    return keep or list(X.columns.astype(str))


def stage3_multicollinearity(X: pd.DataFrame, cols: List[str], cfg: Dict[str, Any]) -> List[str]:
    if len(cols) <= 1:
        return cols
    sub = X[cols].copy()
    for c in sub.columns:
        sub[c] = sub[c].fillna(sub[c].median())
    corr = sub.corr(method="spearman").abs().fillna(0.0)
    thr = float(cfg.get("stage3_spearman_threshold") or 0.92)
    selected = []
    for c in corr.columns:
        if not selected:
            selected.append(str(c))
            continue
        if all(corr.loc[c, s] < thr for s in selected):
            selected.append(str(c))
    return selected


def stage4_relevance(X: pd.DataFrame, y: pd.Series, cols: List[str], cfg: Dict[str, Any]) -> List[str]:
    sub = X[cols].copy()
    yy = pd.to_numeric(y, errors="coerce")
    mask = yy.notna() & sub.notna().all(axis=1)
    if mask.sum() < 40:
        return cols[: int(cfg.get("stage4_top_k") or 12)]
    sub = sub.loc[mask].fillna(0.0)
    yy = yy.loc[mask]

    # Subsample for expensive MI + F computation.
    max_sel = int(cfg.get("max_selection_sample") or 1500)
    sub_s, yy_s = _subsample(sub, yy, max_sel, int(cfg.get("random_state") or 42))

    top_k = min(int(cfg.get("stage4_top_k") or 12), len(cols))

    spearman = {}
    for c in cols:
        v = sub_s[c].corr(yy_s, method="spearman")
        spearman[c] = abs(float(v)) if pd.notna(v) else 0.0

    try:
        mi = mutual_info_regression(
            np.asarray(sub_s.values, dtype=float).copy(),
            np.asarray(yy_s.values, dtype=float).copy(),
            random_state=int(cfg.get("random_state") or 42),
        )
        mi_map = {cols[i]: float(mi[i]) for i in range(len(cols))}
    except Exception:
        mi_map = {c: 0.0 for c in cols}

    try:
        fvals, _ = f_regression(
            np.asarray(sub_s.values, dtype=float).copy(),
            np.asarray(yy_s.values, dtype=float).copy(),
        )
        f_map = {cols[i]: float(fvals[i]) if np.isfinite(fvals[i]) else 0.0 for i in range(len(cols))}
    except Exception:
        f_map = {c: 0.0 for c in cols}

    score = {
        c: spearman.get(c, 0.0) + mi_map.get(c, 0.0) + f_map.get(c, 0.0)
        for c in cols
    }
    ranked = sorted(cols, key=lambda c: score.get(c, 0.0), reverse=True)
    return ranked[:top_k]


def stage5_embedded(X: pd.DataFrame, y: pd.Series, cols: List[str], cfg: Dict[str, Any]) -> List[str]:
    sub = X[cols].copy()
    yy = pd.to_numeric(y, errors="coerce")
    mask = yy.notna() & sub.notna().all(axis=1)
    if mask.sum() < 50:
        return cols
    sub = sub.loc[mask].fillna(0.0)
    yy = yy.loc[mask]

    # Subsample for fast fitting.
    max_sel = int(cfg.get("max_selection_sample") or 1500)
    sub_s, yy_s = _subsample(sub, yy, max_sel, int(cfg.get("random_state") or 42))
    xs = np.asarray(sub_s.values, dtype=float).copy()
    ys = np.asarray(yy_s.values, dtype=float).copy()

    votes: Dict[str, int] = {c: 0 for c in cols}

    try:
        enet = ElasticNetCV(cv=3, random_state=int(cfg.get("random_state") or 42), max_iter=1000)
        enet.fit(xs, ys)
        for i, c in enumerate(cols):
            if abs(float(enet.coef_[i])) > 1e-8:
                votes[c] += 1
    except Exception:
        pass

    try:
        ridge = RidgeCV(alphas=np.logspace(-3, 2, 8))
        ridge.fit(xs, ys)
        for i, c in enumerate(cols):
            if abs(float(ridge.coef_[i])) > 1e-8:
                votes[c] += 1
    except Exception:
        pass

    if cfg.get("stage5_use_rfe", False) and len(cols) > 3:
        try:
            from sklearn.feature_selection import RFE
            from sklearn.linear_model import Ridge

            est = Ridge(alpha=1.0)
            n_keep = max(3, len(cols) // 2)
            rfe = RFE(est, n_features_to_select=n_keep)
            rfe.fit(xs, ys)
            for i, c in enumerate(cols):
                if rfe.support_[i]:
                    votes[c] += 1
        except Exception:
            pass

    min_votes = int(cfg.get("stage5_min_votes") or 1)
    picked = [c for c, v in votes.items() if v >= min_votes]
    return picked or cols[: max(3, len(cols) // 2)]


def stage6_stability(X: pd.DataFrame, y: pd.Series, cols: List[str], cfg: Dict[str, Any]) -> List[str]:
    n_boot = int(cfg.get("stage6_bootstrap_n") or 5)
    thr = float(cfg.get("stage6_stability_threshold") or 0.60)
    counts: Dict[str, int] = {c: 0 for c in cols}
    yy = pd.to_numeric(y, errors="coerce")
    sub_all = X[cols].copy()
    mask = yy.notna() & sub_all.notna().all(axis=1)
    if mask.sum() < 60:
        return cols
    sub_all = sub_all.loc[mask].fillna(0.0)
    yy = yy.loc[mask]

    # Cap bootstrap data size for speed.
    max_sel = int(cfg.get("max_selection_sample") or 1500)
    sub_all, yy = _subsample(sub_all, yy, max_sel, int(cfg.get("random_state") or 42))

    rng = np.random.default_rng(int(cfg.get("random_state") or 42))
    n = len(sub_all)
    for _ in range(n_boot):
        idx = rng.choice(n, size=int(n * 0.8), replace=True)
        sub = sub_all.iloc[idx]
        yb = yy.iloc[idx]
        try:
            enet = ElasticNetCV(cv=3, random_state=42, max_iter=800)
            xs = np.asarray(sub.values, dtype=float).copy()
            yv = np.asarray(yb.values, dtype=float).copy()
            enet.fit(xs, yv)
            for i, c in enumerate(cols):
                if abs(float(enet.coef_[i])) > 1e-8:
                    counts[c] += 1
        except Exception:
            continue
    stable = [c for c, v in counts.items() if v / max(1, n_boot) >= thr]
    return stable or cols


def run_feature_stages(
    df: pd.DataFrame,
    target_tag: str,
    tag_cols: Sequence[str],
    cfg: Dict[str, Any],
) -> Tuple[pd.DataFrame, List[str], Dict[str, Any]]:
    """Return (feature frame, X_final columns, stage trail metadata)."""
    X1 = stage1_raw_features(df, target_tag, tag_cols, cfg)
    s2 = stage2_variance(X1, cfg)
    s3 = stage3_multicollinearity(X1, s2, cfg)
    y = pd.to_numeric(df[target_tag], errors="coerce")
    s4 = stage4_relevance(X1, y, s3, cfg)
    s5 = stage5_embedded(X1, y, s4, cfg)
    s6 = stage6_stability(X1, y, s5, cfg)

    # Hard cap: never pass more than max_features to the model.
    max_feat = int(cfg.get("max_features") or 10)
    if len(s6) > max_feat:
        # Keep the best by Spearman with target.
        yy = pd.to_numeric(df[target_tag], errors="coerce")
        mask = yy.notna()
        yy_v = yy[mask]
        ranked = sorted(
            s6,
            key=lambda c: abs(float(X1.loc[mask, c].corr(yy_v, method="spearman")) or 0)
            if c in X1.columns else 0,
            reverse=True,
        )
        s6 = ranked[:max_feat]

    trail = {
        "stage1_count": len(X1.columns),
        "stage2_count": len(s2),
        "stage3_count": len(s3),
        "stage4_count": len(s4),
        "stage5_count": len(s5),
        "stage6_count": len(s6),
        "X_final": list(s6),
        "stage_sets": {
            "s2": list(s2),
            "s3": list(s3),
            "s4": list(s4),
            "s5": list(s5),
            "s6": list(s6),
        },
    }
    return X1, s6, trail
