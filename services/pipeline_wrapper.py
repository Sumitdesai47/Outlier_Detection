from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd


def _import_pipeline_module(pipeline_py_path: str):
    spec = importlib.util.spec_from_file_location("pipeline_module", pipeline_py_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import Pipeline.py from: {pipeline_py_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def run_root_cause_pipeline(
    *,
    time_series_df: pd.DataFrame,
    target_col: str,
    candidate_causes: Sequence[str],
    example_paths: Sequence[str],
    historic_ratio: float = 0.70,
    top_n_root_causes: int = 10,
    pipeline_py_path: Optional[str] = None,
) -> dict:
    """
    Wrap your existing `generic_root_cause_pipeline(config)` from Pipeline.py.

    The wrapper converts:
      - time_series_df -> temp CSV (with Timestamp + target + candidate causes columns)
      - example_paths + candidate_causes -> temp Excel sheets: All_Causes, Example_Paths
    and returns Pipeline's result dict.
    """
    if "Timestamp" not in time_series_df.columns:
        raise ValueError("time_series_df must include a `Timestamp` column.")
    if target_col not in time_series_df.columns:
        raise ValueError(f"target_col '{target_col}' not found in time_series_df.")

    candidate_causes_used = sorted({str(c).strip() for c in candidate_causes if str(c).strip()})
    candidate_causes_used = [c for c in candidate_causes_used if c in time_series_df.columns and c != target_col]

    # Cap example_paths to keep temp files smaller.
    example_paths = [str(p).strip() for p in example_paths if str(p).strip()]
    example_paths = list(dict.fromkeys(example_paths))[:2000]

    if pipeline_py_path is None:
        pipeline_py_path = str(Path(__file__).resolve().parents[1] / "Pipeline.py")

    start_ts = pd.to_datetime(time_series_df["Timestamp"].min(), errors="coerce")
    end_ts = pd.to_datetime(time_series_df["Timestamp"].max(), errors="coerce")
    if pd.isna(start_ts) or pd.isna(end_ts):
        # If timestamp parsing fails, fall back to a wide window so Pipeline.py doesn't filter everything out.
        start_date_str = "1900-01-01 00:00:00"
        end_date_str = "2100-01-01 00:00:00"
    else:
        # Preserve sub-second precision to avoid filtering out nearly-equal nanosecond timestamps.
        start_date_str = start_ts.isoformat(sep=" ")
        end_date_str = end_ts.isoformat(sep=" ")

    # `ignore_cleanup_errors=True` avoids 500s when Windows keeps a file handle open
    # after Excel reading/writing (seen as WinError 32 during cleanup).
    with tempfile.TemporaryDirectory(prefix="anomaly_dash_", ignore_cleanup_errors=True) as tmpdir:
        csv_path = os.path.join(tmpdir, "time_series.csv")
        cause_xlsx_path = os.path.join(tmpdir, "causes_paths.xlsx")
        output_xlsx_path = os.path.join(tmpdir, "pipeline_output.xlsx")

        cols = ["Timestamp", target_col] + candidate_causes_used
        work_df = time_series_df.loc[:, cols].copy()
        work_df.to_csv(csv_path, index=False)

        # Build cause file that Pipeline.py expects.
        all_causes_df = pd.DataFrame({ "Cause": candidate_causes_used })
        example_paths_df = pd.DataFrame({ "Propagation_Path": example_paths })

        with pd.ExcelWriter(cause_xlsx_path, engine="openpyxl") as writer:
            all_causes_df.to_excel(writer, sheet_name="All_Causes", index=False)
            example_paths_df.to_excel(writer, sheet_name="Example_Paths", index=False)

        pipeline_module = _import_pipeline_module(pipeline_py_path)
        base_config = getattr(pipeline_module, "CONFIG", {})
        config = dict(base_config)  # copy

        config.update(
            {
                "data_file": csv_path,
                "cause_file": cause_xlsx_path,
                "output_file": output_xlsx_path,
                "timestamp_col": "Timestamp",
                "target_col": target_col,
                "start_date": start_date_str,
                "end_date": end_date_str,
                "historic_ratio": float(historic_ratio),
                "top_n_root_causes": int(top_n_root_causes),
                "use_all_causes_sheet": True,
                "all_causes_sheet_name": "All_Causes",
                "use_example_paths_sheet": True,
                "example_paths_sheet_name": "Example_Paths",
            }
        )

        results = pipeline_module.generic_root_cause_pipeline(config)

        # Keep only what the UI needs (avoid huge objects in-memory).
        # Note: Pipeline returns DataFrames. We pass them back and let the UI slice/select.
        # Note: temp directory is deleted when leaving this context, so we don't
        # return the output Excel path to avoid pointing to a missing file.
        return {
            "candidate_causes_used": candidate_causes_used,
            "example_paths_used": len(example_paths),
            "results": results,
        }

