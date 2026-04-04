"""Backward-compatible re-exports (prefer services.anomaly_pipeline and services.part2_plots)."""
from __future__ import annotations

from .anomaly_pipeline import compute_top10_roots_with_paths, run_drift_phase_from_uploads
from .detail_pipeline_prep import (
    fmt_ts_cell,
    graph_from_propagation_paths,
    load_causal_graph_from_chain_matrix_excel,
    load_detail_pipeline_module,
    prepare_smoothed_from_wide_df,
    serialize_row,
)
from .part2_plots import build_part2_target_plot_json

__all__ = [
    "build_part2_target_plot_json",
    "compute_top10_roots_with_paths",
    "fmt_ts_cell",
    "graph_from_propagation_paths",
    "load_causal_graph_from_chain_matrix_excel",
    "load_detail_pipeline_module",
    "prepare_smoothed_from_wide_df",
    "run_drift_phase_from_uploads",
    "serialize_row",
]
