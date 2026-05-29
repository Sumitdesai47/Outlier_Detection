"""Linear vs nonlinear model selection and CV winner."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import ElasticNetCV, LassoCV, Ridge, RidgeCV
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import KFold
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR

try:
    from statsmodels.stats.diagnostic import linear_reset
except Exception:
    linear_reset = None


@dataclass
class ModelBundle:
    model: Any
    scaler: StandardScaler
    features: List[str]
    model_name: str
    model_type: str
    cv_rmse: float
    candidates: List[Dict[str, Any]]
    selection_reason: str = ""
    cv_r2: Optional[float] = None


def _cv_metrics(
    model, X: np.ndarray, y: np.ndarray, folds: int, seed: int
) -> Tuple[float, float]:
    kf = KFold(n_splits=max(2, folds), shuffle=True, random_state=seed)
    rmse_scores: List[float] = []
    r2_scores: List[float] = []
    for tr, te in kf.split(X):
        model.fit(X[tr], y[tr])
        pred = model.predict(X[te])
        rmse_scores.append(float(np.sqrt(mean_squared_error(y[te], pred))))
        r2_scores.append(float(r2_score(y[te], pred)))
    rmse = float(np.mean(rmse_scores)) if rmse_scores else np.inf
    r2 = float(np.mean(r2_scores)) if r2_scores else float("nan")
    return rmse, r2


def _feature_pearson_spearman(
    sub: pd.DataFrame, yy: pd.Series
) -> List[Dict[str, float]]:
    """Per-feature |Pearson| and |Spearman| vs target."""
    rows: List[Dict[str, float]] = []
    for col in sub.columns:
        s = sub[col]
        pearson = s.corr(yy, method="pearson")
        spearman = s.corr(yy, method="spearman")
        if pd.isna(pearson) and pd.isna(spearman):
            continue
        rows.append(
            {
                "pearson": abs(float(pearson)) if pd.notna(pearson) else 0.0,
                "spearman": abs(float(spearman)) if pd.notna(spearman) else 0.0,
            }
        )
    return rows


def assess_model_path_from_correlations(
    X: pd.DataFrame, y: pd.Series, cfg: Dict[str, Any]
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Choose linear vs nonlinear using Pearson and Spearman on final features.

    Nonlinear when top-ranked features (by Spearman) mostly show weak Pearson
    (< threshold) but stronger Spearman — monotonic / rank-driven relationship.
    """
    pearson_thr = float(cfg.get("pearson_linear_threshold") or 0.5)
    spearman_excess = float(cfg.get("spearman_excess_min") or 0.05)
    top_k = max(1, int(cfg.get("nonlinear_top_feature_k") or 5))
    min_nonlinear_count = max(1, int(cfg.get("nonlinear_top_feature_min_count") or 2))
    pearson_median_thr = float(cfg.get("pearson_linear_median_threshold") or 0.5)

    prof = _feature_pearson_spearman(X, y)
    diag: Dict[str, Any] = {
        "n_features": len(prof),
        "pearson_threshold": pearson_thr,
        "top_k": top_k,
    }
    if not prof:
        return "linear", "No usable feature correlations — defaulting to linear models.", diag

    # Rank by largest Spearman−Pearson gap (rank-driven / monotone signal vs linear).
    prof_sorted = sorted(
        prof, key=lambda r: r["spearman"] - r["pearson"], reverse=True
    )
    top = prof_sorted[: min(top_k, len(prof_sorted))]
    diag["median_pearson"] = round(float(np.median([r["pearson"] for r in prof])), 4)
    diag["median_spearman"] = round(float(np.median([r["spearman"] for r in prof])), 4)

    # Nonlinear signal: Pearson weak but Spearman clearly stronger (rank/monotone pattern).
    nonlinear_flags = [
        r
        for r in top
        if r["pearson"] < pearson_thr
        and r["spearman"] > r["pearson"] + spearman_excess
    ]
    diag["top_nonlinear_pattern_count"] = len(nonlinear_flags)
    diag["top_features_checked"] = len(top)

    if len(nonlinear_flags) >= min_nonlinear_count:
        med_p = float(diag["median_pearson"])
        med_s = float(diag["median_spearman"])
        return (
            "nonlinear",
            (
                f"Top {len(top)} features: {len(nonlinear_flags)} show Pearson < {pearson_thr:.2f} "
                f"with Spearman > Pearson (median Pearson={med_p:.3f}, median Spearman={med_s:.3f}) "
                f"— nonlinear models evaluated."
            ),
            diag,
        )

    # Strong linear Pearson across features → linear path.
    if (
        diag["median_pearson"] >= pearson_median_thr
        and diag["median_spearman"] <= diag["median_pearson"] + spearman_excess
    ):
        return (
            "linear",
            (
                f"Median |Pearson|={diag['median_pearson']:.3f} >= {pearson_median_thr:.2f} and "
                f"Spearman is not materially higher — linear models are sufficient."
            ),
            diag,
        )

    # Top features agree on strong Pearson (similar linear behaviour).
    top_linear_like = sum(
        1
        for r in top
        if r["pearson"] >= pearson_thr and r["spearman"] <= r["pearson"] + spearman_excess
    )
    if top_linear_like >= min_nonlinear_count and top_linear_like >= len(top) - 1:
        return (
            "linear",
            (
                f"Top selected features show similar linear behaviour "
                f"(|Pearson| >= {pearson_thr:.2f}, Spearman ~ Pearson)."
            ),
            diag,
        )

    return "nonlinear", (
        "Pearson/Spearman profile does not support a purely linear mapping — "
        "nonlinear models evaluated."
    ), diag


def select_model_type(X: pd.DataFrame, y: pd.Series, cfg: Dict[str, Any]) -> Tuple[str, str]:
    """Return ('linear'|'nonlinear', plain-language reason)."""
    yy = pd.to_numeric(y, errors="coerce")
    mask = yy.notna() & X.notna().all(axis=1)
    n = int(mask.sum())
    if n < int(cfg.get("linear_min_sample_size") or 200):
        return "linear", f"Sample size ({n}) below linear minimum — using linear models for stability."

    sub = X.loc[mask].fillna(0.0)
    yy = yy.loc[mask]

    path, reason, _diag = assess_model_path_from_correlations(sub, yy, cfg)
    if path == "nonlinear":
        return path, reason

    scaler = StandardScaler()
    Xs = scaler.fit_transform(np.asarray(sub.values, dtype=float).copy())
    ys = np.asarray(yy.values, dtype=float).copy()

    if linear_reset is not None:
        try:
            import statsmodels.api as sm

            Xsm = sm.add_constant(Xs)
            ols = sm.OLS(ys, Xsm).fit()
            _, pval, _ = linear_reset(ols, power=2, use_f=True)
            if pval is not None and float(pval) < 0.05:
                return (
                    "nonlinear",
                    f"Ramsey RESET suggests nonlinearity (p={float(pval):.4f}) — nonlinear models evaluated.",
                )
        except Exception:
            pass

    return path, reason


def _train_candidates(model_type: str, cfg: Dict[str, Any]) -> List[Tuple[str, Any]]:
    rs = int(cfg.get("random_state") or 42)
    if model_type == "linear":
        return [
            ("ElasticNetCV", ElasticNetCV(cv=3, random_state=rs, max_iter=3000)),
            ("RidgeCV", RidgeCV(alphas=np.logspace(-3, 3, 15))),
            ("LassoCV", LassoCV(cv=3, random_state=rs, max_iter=3000)),
        ]
    return [
        ("GradientBoosting", GradientBoostingRegressor(random_state=rs, max_depth=4, n_estimators=120)),
        ("RandomForest", RandomForestRegressor(random_state=rs, n_estimators=120, max_depth=8, n_jobs=-1)),
        ("SVR_RBF", SVR(kernel="rbf", C=1.0, epsilon=0.1)),
    ]


def train_winner(
    X: pd.DataFrame,
    y: pd.Series,
    features: List[str],
    cfg: Dict[str, Any],
) -> ModelBundle:
    """Pick lowest CV RMSE model; fall back to Ridge on failure."""
    feats = [f for f in features if f in X.columns]
    if not feats:
        raise ValueError("No features available for model training.")

    yy = pd.to_numeric(y, errors="coerce")
    mask = yy.notna() & X[feats].notna().all(axis=1)
    if int(mask.sum()) < int(cfg.get("min_train_rows") or 80):
        raise ValueError("Not enough rows to train multimodel.")

    sub = X.loc[mask, feats].fillna(0.0)
    yy = yy.loc[mask]
    ys = np.asarray(yy.values, dtype=float).copy()
    model_type, selection_reason = select_model_type(sub, yy, cfg)
    folds = int(cfg.get("cv_folds") or 5)
    seed = int(cfg.get("random_state") or 42)

    scaler = StandardScaler()
    Xs = scaler.fit_transform(np.asarray(sub.values, dtype=float).copy())

    best_name = "Ridge"
    best_rmse = np.inf
    best_model: Any = Ridge(alpha=1.0)
    candidate_rows: List[Dict[str, Any]] = []

    linear_names = [n for n, _ in _train_candidates("linear", cfg)]
    nonlinear_names = [n for n, _ in _train_candidates("nonlinear", cfg)]
    skipped_family = "nonlinear" if model_type == "linear" else "linear"
    skipped_names = nonlinear_names if skipped_family == "nonlinear" else linear_names
    for name in skipped_names:
        candidate_rows.append(
            {
                "model_name": name,
                "model_family": skipped_family,
                "cv_rmse": None,
                "cv_r2": None,
                "status": "skipped",
            }
        )

    best_r2 = float("nan")
    for name, est in _train_candidates(model_type, cfg):
        try:
            rmse, r2 = _cv_metrics(est, Xs, ys, folds, seed)
            candidate_rows.append(
                {
                    "model_name": name,
                    "model_family": model_type,
                    "cv_rmse": round(float(rmse), 4) if np.isfinite(rmse) else None,
                    "cv_r2": round(float(r2), 3) if np.isfinite(r2) else None,
                    "status": "done",
                }
            )
            if rmse < best_rmse:
                best_rmse = rmse
                best_r2 = r2
                best_name = name
                best_model = est
        except Exception:
            candidate_rows.append(
                {
                    "model_name": name,
                    "model_family": model_type,
                    "cv_rmse": None,
                    "cv_r2": None,
                    "status": "failed",
                }
            )
            continue

    best_model.fit(Xs, ys)
    for row in candidate_rows:
        row["is_winner"] = row.get("model_name") == best_name and row.get("status") == "done"
    return ModelBundle(
        model=best_model,
        scaler=scaler,
        features=feats,
        model_name=best_name,
        model_type=model_type,
        cv_rmse=float(best_rmse),
        candidates=candidate_rows,
        selection_reason=selection_reason,
        cv_r2=float(best_r2) if np.isfinite(best_r2) else None,
    )
