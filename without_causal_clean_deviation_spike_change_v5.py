"""
WITHOUT-CAUSAL — deviation / spike / change V5 (stable name for app integration).

Implements the same pipeline as ``without_causal_clean_deviation_no_mavg_inlimit_v5``:
clean period without moving average, clean-like limits, outside-limit and
within-limit spike / error-change / persistent deviation detection, run-length
persistence.

Run (CLI defaults use this module's CONFIG)::

    python without_causal_clean_deviation_spike_change_v5.py

For programmatic use, import ``CONFIG`` and the re-exported callables below.
"""

from __future__ import annotations

import copy

import without_causal_clean_deviation_no_mavg_inlimit_v5 as _core

CONFIG = copy.deepcopy(_core.CONFIG)
CONFIG["output_file"] = "without_causal_clean_deviation_spike_change_v5_result.xlsx"

detect_clean_period_no_mavg = _core.detect_clean_period_no_mavg
build_clean_like_limits = _core.build_clean_like_limits
generate_without_causal_all_results = _core.generate_without_causal_all_results
create_row_status = _core.create_row_status
create_summary = _core.create_summary
create_tag_summary = _core.create_tag_summary
read_excel_file = _core.read_excel_file
clean_column_names = _core.clean_column_names
find_column = _core.find_column
write_output_excel = _core.write_output_excel


def main(config: dict) -> dict:
    """Same pipeline as the core V5 script; ``config`` should be a deep copy of CONFIG if mutated."""
    return _core.main(config)


def parse_args():
    return _core.parse_args()


if __name__ == "__main__":
    args = parse_args()
    cfg = copy.deepcopy(CONFIG)
    cfg["data_file"] = args.data_file
    cfg["benchmark_result_file"] = args.benchmark_file
    # argparse defaults follow core module CONFIG; prefer this module's default output name
    if args.output_file == _core.CONFIG.get("output_file"):
        cfg["output_file"] = CONFIG["output_file"]
    else:
        cfg["output_file"] = args.output_file
    cfg["data_sheet_name"] = args.data_sheet_name
    cfg["benchmark_sheet_name"] = args.benchmark_sheet_name
    cfg["timestamp_col"] = args.timestamp_col
    cfg["candidate_z_limit"] = args.candidate_z_limit
    cfg["threshold_k"] = args.threshold_k
    cfg["limit_margin"] = args.limit_margin
    cfg["delta_spike_z"] = args.delta_spike_z
    cfg["error_change_z"] = args.error_change_z
    cfg["inlimit_deviation_z"] = args.inlimit_deviation_z
    cfg["outside_persistence_points"] = args.outside_persistence_points
    cfg["inlimit_deviation_persistence_points"] = args.inlimit_deviation_persistence_points
    main(cfg)
