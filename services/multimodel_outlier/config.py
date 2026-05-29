"""Default configuration for multimodel feature selection + S5 prediction."""
from __future__ import annotations

import os
from typing import Any, Dict

# ------------------------------------------------------------------
# Performance-tuned defaults:
#   - cap features at 10 (max_features)
#   - subsample large datasets before expensive ops
#   - 3-fold CV instead of 5
#   - 5 bootstrap rounds instead of 12
#   - RFE disabled (too slow for routine runs)
#   - parallel tag training up to half available CPUs
# ------------------------------------------------------------------
DEFAULT_CFG: Dict[str, Any] = {
    # Stage 1 feature engineering
    "rolling_windows": [9, 31],        # drop 72-window (saves 4 features, ~15% stage-1 time)
    "max_peer_features": 5,            # top-5 peer deltas only (was 8)
    "early_segment_fraction": 0.28,

    # Stage 2 — variance filter
    "stage2_min_variance": 1e-8,
    "stage2_max_missing_frac": 0.35,

    # Stage 3 — multicollinearity
    "stage3_spearman_threshold": 0.92,
    "stage3_max_vif": 12.0,

    # Stage 4 — relevance (top-K)
    "stage4_top_k": 12,                # pre-filter to 12 before embedded (was 20)

    # Stage 5 — embedded vote
    "stage5_min_votes": 1,             # 1 of 2 voters is enough (was 2; RFE removed)
    "stage5_use_rfe": False,           # RFE is the slowest part — off by default

    # Stage 6 — bootstrap stability
    "stage6_bootstrap_n": 5,           # 5 rounds (was 12)
    "stage6_stability_threshold": 0.60,

    # Hard cap on final features passed to the model
    "max_features": 10,

    # Subsampling for expensive ops (MI, bootstrap, model training)
    "max_train_sample": 2000,          # subsample rows when n > this
    "max_selection_sample": 1500,      # subsample for stage 4 MI + stage 5 votes

    # Linear vs nonlinear path (Pearson + Spearman)
    "linear_min_sample_size": 150,
    "pearson_linear_threshold": 0.5,
    "spearman_excess_min": 0.05,
    "nonlinear_top_feature_k": 5,
    "nonlinear_top_feature_min_count": 2,
    "pearson_linear_median_threshold": 0.5,

    # Model training
    "cv_folds": 3,                     # 3-fold CV (was 5)
    "random_state": 42,
    "ridge_alpha": 1.0,
    "min_train_rows": 60,              # was 80

    # S5 residual z-score
    "k_peer_residual_z": 3.75,
    "min_mad": 1e-6,

    # Parallelism: max worker threads for per-tag training
    # 0 = auto (half the logical CPUs, at least 2)
    "n_parallel_workers": int(os.environ.get("MM_PARALLEL_WORKERS", "0")),
}
