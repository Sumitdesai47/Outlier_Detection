"""Default configuration for multimodel feature selection + S5 prediction."""
from __future__ import annotations

from typing import Any, Dict

DEFAULT_CFG: Dict[str, Any] = {
    "rolling_windows": [9, 31, 72],
    "max_peer_features": 8,
    "early_segment_fraction": 0.28,
    "stage2_min_variance": 1e-8,
    "stage2_max_missing_frac": 0.35,
    "stage3_spearman_threshold": 0.92,
    "stage3_max_vif": 12.0,
    "stage4_top_k": 20,
    "stage5_min_votes": 2,
    "stage5_use_rfe": True,
    "stage6_bootstrap_n": 12,
    "stage6_stability_threshold": 0.70,
    "linear_min_sample_size": 200,
    # Linear vs nonlinear path (Pearson + Spearman on final features).
    "pearson_linear_threshold": 0.5,
    "spearman_excess_min": 0.05,
    "nonlinear_top_feature_k": 5,
    "nonlinear_top_feature_min_count": 2,
    "pearson_linear_median_threshold": 0.5,
    "cv_folds": 5,
    "random_state": 42,
    "ridge_alpha": 1.0,
    "min_train_rows": 80,
    "k_peer_residual_z": 3.75,
    "min_mad": 1e-6,
}
