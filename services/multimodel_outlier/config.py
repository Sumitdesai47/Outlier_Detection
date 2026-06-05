"""Default configuration for multimodel cluster-based S5 prediction."""
from __future__ import annotations

import os
from typing import Any, Dict

# Cluster methodology (Cluster_Methodology_Document) + linear/nonlinear CV winner.
DEFAULT_CFG: Dict[str, Any] = {
    # Cluster / dynamic peer selection (raw tags as X — no feature engineering)
    "max_features": 10,
    "dynamic_top_n_correlation": 10,
    "dynamic_top_n_mutual_info": 10,
    "dynamic_top_n_lag": 10,
    "dynamic_max_lag": 5,
    "dynamic_cluster_distance_threshold": 0.5,
    "min_peer_abs_corr": 0.15,

    # Subsampling for model training
    "max_train_sample": 2000,
    "max_selection_sample": 1500,

    # Linear vs nonlinear path (Pearson + Spearman on selected peer tags)
    "linear_min_sample_size": 150,
    "pearson_linear_threshold": 0.5,
    "spearman_excess_min": 0.05,
    "nonlinear_top_feature_k": 5,
    "nonlinear_top_feature_min_count": 2,
    "pearson_linear_median_threshold": 0.5,

    # Model training (multiple candidates, lowest CV RMSE wins)
    "cv_folds": 3,
    "random_state": 42,
    "ridge_alpha": 1.0,
    "min_train_rows": 60,

    # S5 residual z-score
    "k_peer_residual_z": 3.75,
    "min_mad": 1e-6,

    # Parallelism: max worker threads for per-tag training
    "n_parallel_workers": int(os.environ.get("MM_PARALLEL_WORKERS", "0")),
}
