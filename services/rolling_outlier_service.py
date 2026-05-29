"""Rolling/day-by-day outlier detection using Dev outlier logic."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from services.robust_consensus_outlier_workflow import (
    MULTI_SIGNAL_PRESET,
    run_multi_signal_outlier_detection,
)


def _to_wide_df(observations: List[Dict[str, Any]]) -> pd.DataFrame:
    if not observations:
        return pd.DataFrame()
    df = pd.DataFrame(observations)
    if df.empty:
        return df
    pivot = (
        df.pivot_table(
            index=["row_index", "observed_at", "observed_at_raw"],
            columns="tag_name",
            values="value",
            aggfunc="last",
        )
        .reset_index()
        .sort_values("row_index")
    )
    pivot.columns = [str(c) for c in pivot.columns]
    pivot = pivot.rename(columns={"observed_at": "Timestamp"})
    pivot["Timestamp"] = pd.to_datetime(pivot["Timestamp"], errors="coerce")
    pivot = pivot.dropna(subset=["Timestamp"]).reset_index(drop=True)
    return pivot


def _current_abnormal_map(bundle: Dict[str, Any], ts: pd.Timestamp) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    details = bundle.get("details_by_tag") or {}
    for tag, rows in details.items():
        for r in rows or []:
            r_ts = pd.to_datetime(r.get("Timestamp"), errors="coerce")
            if pd.isna(r_ts):
                continue
            if pd.Timestamp(r_ts) != ts:
                continue
            cls = str(r.get("Final_Class") or "Normal").strip() or "Normal"
            out[str(tag)] = {"final_class": cls, "reason": str(r.get("Reason") or "")}
            break
    return out


def run_rolling_detection(
    wide_df: pd.DataFrame,
    *,
    window_size: int = 30,
    window_mode: str = "rolling",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if wide_df is None or wide_df.empty:
        raise ValueError("No time-series rows found for selected dataset.")
    if "Timestamp" not in wide_df.columns:
        raise ValueError("Timestamp column missing in dataset.")
    if len(wide_df) <= window_size:
        raise ValueError(f"Need more than {window_size} rows for rolling detection.")

    work = wide_df.copy()
    work["Timestamp"] = pd.to_datetime(work["Timestamp"], errors="coerce")
    work = work.dropna(subset=["Timestamp"]).sort_values("Timestamp").reset_index(drop=True)
    tag_cols = [c for c in work.columns if c not in ("row_index", "Timestamp", "observed_at_raw")]
    numeric_tags: List[str] = []
    for c in tag_cols:
        s = pd.to_numeric(work[c], errors="coerce")
        if s.notna().sum() >= max(window_size, 10):
            work[c] = s
            numeric_tags.append(str(c))
    if not numeric_tags:
        raise ValueError("No usable numeric tags in dataset.")

    k = float(MULTI_SIGNAL_PRESET.get("k_global_robust_z", 3.75))
    records: List[Dict[str, Any]] = []
    processed_rows = 0

    for i in range(window_size, len(work)):
        if window_mode == "rolling":
            start = max(0, i - window_size)
        else:
            start = 0
        step = work.iloc[start : i + 1].copy()
        baseline = work.iloc[start:i].copy()
        ts = pd.Timestamp(work.at[i, "Timestamp"])
        raw_ts = work.at[i, "observed_at_raw"] if "observed_at_raw" in work.columns else None
        row_idx = int(work.at[i, "row_index"]) if "row_index" in work.columns and pd.notna(work.at[i, "row_index"]) else i

        # Use the same core Dev outlier methodology/preset as part15.
        bundle = run_multi_signal_outlier_detection(step, config=MULTI_SIGNAL_PRESET)
        abnormal = _current_abnormal_map(bundle, ts)

        for tag in numeric_tags:
            val = pd.to_numeric(work.at[i, tag], errors="coerce")
            b = pd.to_numeric(baseline[tag], errors="coerce").dropna()
            mean = float(b.mean()) if not b.empty else np.nan
            std = float(b.std(ddof=0)) if len(b) > 1 else np.nan
            z = ((float(val) - mean) / std) if np.isfinite(std) and std > 0 and np.isfinite(val) else np.nan
            lower = mean - k * std if np.isfinite(mean) and np.isfinite(std) else np.nan
            upper = mean + k * std if np.isfinite(mean) and np.isfinite(std) else np.nan

            cls = (abnormal.get(tag) or {}).get("final_class", "Normal")
            reason = (abnormal.get(tag) or {}).get("reason", "Within dev consensus limits.")
            status = "Outlier" if cls not in ("Normal", "", "Spike - Returned Normal") else "Normal"

            records.append(
                {
                    "row_index": int(row_idx),
                    "observed_at": ts.to_pydatetime(),
                    "observed_at_raw": str(raw_ts) if raw_ts is not None else None,
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
        processed_rows += 1

    meta = {
        "processed_timestamps": processed_rows,
        "tags_count": len(numeric_tags),
        "window_size": int(window_size),
        "window_mode": str(window_mode),
    }
    return records, meta


def build_wide_from_observation_rows(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    return _to_wide_df(rows)

