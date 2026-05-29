"""Rolling/day-by-day outlier processing using Dev outlier logic."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from services.robust_consensus_outlier_workflow import MULTI_SIGNAL_PRESET
from services.streamlit_dev_outlier_pipeline import run_multi_signal_outlier_detection


def _is_outlier_class(final_class: str) -> bool:
    c = str(final_class or "").strip()
    return c not in ("", "Normal", "Spike - Returned Normal")


def _window_slice(df: pd.DataFrame, idx: int, mode: str, window_size: int) -> pd.DataFrame:
    if mode == "rolling":
        start = max(0, idx - window_size)
    else:
        start = 0
    return df.iloc[start : idx + 1].copy()


def run_rolling_outlier_detection(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    window_size: int = 30,
    window_mode: str = "rolling",
) -> Dict[str, Any]:
    """
    Process each timestamp from row (window_size + 1) onwards.

    For each step:
    - baseline stats are computed on the baseline window
    - status/reason come from Dev outlier logic on the same step window
    """
    if df is None or df.empty:
        raise ValueError("Input dataset is empty.")
    if "Timestamp" not in df.columns:
        raise ValueError("Dataset must contain a Timestamp column.")
    if len(df) <= window_size:
        raise ValueError(f"Need more than {window_size} rows for rolling analysis.")

    work = df.copy()
    work["Timestamp"] = pd.to_datetime(work["Timestamp"], errors="coerce")
    work = work.dropna(subset=["Timestamp"]).sort_values("Timestamp").reset_index(drop=True)
    tag_cols = [c for c in work.columns if c != "Timestamp"]
    numeric_tags = []
    for c in tag_cols:
        s = pd.to_numeric(work[c], errors="coerce")
        if s.notna().sum() >= max(10, window_size):
            work[c] = s
            numeric_tags.append(c)
    if not numeric_tags:
        raise ValueError("No numeric tag columns available after cleaning.")

    run_id = uuid.uuid4().hex
    k = float(MULTI_SIGNAL_PRESET.get("k_global_robust_z", 3.75))
    records: List[Dict[str, Any]] = []

    for idx in range(window_size, len(work)):
        step = _window_slice(work, idx, window_mode, window_size)
        current_ts = pd.Timestamp(work.at[idx, "Timestamp"])

        # Use Dev outlier logic for final status/reason.
        bundle = run_multi_signal_outlier_detection(step, config=MULTI_SIGNAL_PRESET)
        details = bundle.get("details_by_tag") or {}
        current_map: Dict[str, Dict[str, Any]] = {}
        for tag, rows in details.items():
            for r in rows or []:
                r_ts = pd.to_datetime(r.get("Timestamp"), errors="coerce")
                if pd.isna(r_ts):
                    continue
                if pd.Timestamp(r_ts) == current_ts:
                    current_map[str(tag)] = {
                        "final_class": str(r.get("Final_Class") or "Normal"),
                        "reason": str(r.get("Reason") or ""),
                    }
                    break

        if window_mode == "rolling":
            baseline = work.iloc[max(0, idx - window_size) : idx]
        else:
            baseline = work.iloc[:idx]

        for tag in numeric_tags:
            val = pd.to_numeric(work.at[idx, tag], errors="coerce")
            base_series = pd.to_numeric(baseline[tag], errors="coerce").dropna()
            mean = float(base_series.mean()) if not base_series.empty else np.nan
            std = float(base_series.std(ddof=0)) if len(base_series) > 1 else np.nan
            z = ((float(val) - mean) / std) if np.isfinite(std) and std > 0 and np.isfinite(val) else np.nan
            lower = mean - k * std if np.isfinite(mean) and np.isfinite(std) else np.nan
            upper = mean + k * std if np.isfinite(mean) and np.isfinite(std) else np.nan

            cls = (current_map.get(tag) or {}).get("final_class", "Normal")
            reason = (current_map.get(tag) or {}).get("reason", "Within dev consensus limits.")
            status = "Outlier" if _is_outlier_class(cls) else "Normal"

            records.append(
                {
                    "run_id": run_id,
                    "dataset_name": dataset_name,
                    "window_mode": window_mode,
                    "window_size": int(window_size),
                    "ts": current_ts.isoformat(),
                    "tag_name": str(tag),
                    "tag_value": float(val) if np.isfinite(val) else None,
                    "baseline_mean": mean if np.isfinite(mean) else None,
                    "baseline_std": std if np.isfinite(std) else None,
                    "z_score": z if np.isfinite(z) else None,
                    "lower_limit": lower if np.isfinite(lower) else None,
                    "upper_limit": upper if np.isfinite(upper) else None,
                    "status": status,
                    "reason": f"{cls}: {reason}",
                }
            )

    return {
        "run_id": run_id,
        "dataset_name": dataset_name,
        "window_mode": window_mode,
        "window_size": int(window_size),
        "processed_timestamps": max(0, len(work) - window_size),
        "tags_count": len(numeric_tags),
        "records": records,
    }

