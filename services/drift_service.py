from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .time_series_utils import safe_parse_datetime_series, load_wide_time_series_xlsx


def safe_numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def compute_drift_metrics(ref: pd.Series, cur: pd.Series) -> dict:
    """
    Drift metrics aligned with your `Pipeline.py` logic:
    - KS test statistic + p_value
    - relative mean/std shifts
    - drift_flag based on p<0.05 OR mean/std shifts > 0.20
    """
    from scipy.stats import ks_2samp

    ref = ref.dropna()
    cur = cur.dropna()

    if len(ref) < 5 or len(cur) < 5:
        return {
            "ks_stat": np.nan,
            "p_value": np.nan,
            "mean_shift": np.nan,
            "std_shift": np.nan,
            "drift_flag": False,
        }

    ks_stat, p_value = ks_2samp(ref, cur)

    ref_mean = ref.mean()
    ref_std = ref.std()
    cur_mean = cur.mean()
    cur_std = cur.std()

    mean_shift = abs(cur_mean - ref_mean) / (abs(ref_mean) + 1e-6)
    std_shift = abs(cur_std - ref_std) / (abs(ref_std) + 1e-6)

    drift_flag = (p_value < 0.05) or (mean_shift > 0.20) or (std_shift > 0.20)

    return {
        "ks_stat": ks_stat,
        "p_value": p_value,
        "mean_shift": mean_shift,
        "std_shift": std_shift,
        "drift_flag": drift_flag,
    }


def detect_first_drift_time(df: pd.DataFrame, col: str, split_index: int, timestamp_col: str) -> Optional[pd.Timestamp]:
    hist = df.iloc[:split_index]
    cur = df.iloc[split_index:]

    ref = hist[col].dropna()
    if len(ref) < 5:
        return None

    ref_mean = ref.mean()
    ref_std = ref.std()

    upper = ref_mean + 3 * ref_std
    lower = ref_mean - 3 * ref_std

    drift_rows = cur[(cur[col] > upper) | (cur[col] < lower)]
    if len(drift_rows) == 0:
        return None

    return drift_rows.iloc[0][timestamp_col]


def rank_drift_tags(
    time_series_xlsx_path: str,
    *,
    target_col: str,
    historic_ratio: float = 0.70,
    top_k: int = 10,
    sheet_name=0,
) -> dict:
    """
    For each tag column (excluding the target):
    - split historic/current by `historic_ratio`
    - compute drift metrics vs historic/current
    - rank by a drift magnitude score
    Returns:
      - top_tags_df: DataFrame of top_k tags with drift metrics and drift time
      - target_drift_metrics/time
    """
    df = load_wide_time_series_xlsx(
        time_series_xlsx_path, sheet_name=sheet_name, timestamp_col_name="Timestamp"
    )
    if target_col not in df.columns:
        raise ValueError(f"Target column '{target_col}' not found in time-series XLSX.")

    df = df.copy()
    df["Timestamp"] = safe_parse_datetime_series(df["Timestamp"])
    # Ensure numeric for tags/target.
    for c in df.columns:
        if c == "Timestamp":
            continue
        df[c] = safe_numeric_series(df[c])

    df = df.dropna(subset=[target_col]).reset_index(drop=True)
    split_index = int(len(df) * historic_ratio)
    split_index = max(1, min(split_index, len(df) - 1))

    historic = df.iloc[:split_index].copy()
    current = df.iloc[split_index:].copy()

    target_drift_metrics = compute_drift_metrics(historic[target_col], current[target_col])
    target_drift_time = detect_first_drift_time(df, target_col, split_index, "Timestamp")

    tags = [c for c in df.columns if c not in ["Timestamp", target_col]]
    rows = []
    for tag in tags:
        metrics = compute_drift_metrics(historic[tag], current[tag])
        drift_time = detect_first_drift_time(df, tag, split_index, "Timestamp")
        drift_magnitude = (abs(metrics.get("mean_shift", 0) or 0) + abs(metrics.get("std_shift", 0) or 0))

        rows.append(
            {
                "X_Tag": tag,
                "X_Drift_Flag": metrics["drift_flag"],
                "X_Drift_Time": drift_time,
                "KS_Stat": metrics["ks_stat"],
                "p_value": metrics["p_value"],
                "Mean_Shift": metrics["mean_shift"],
                "Std_Shift": metrics["std_shift"],
                "Drift_Magnitude": drift_magnitude,
            }
        )

    drift_df = pd.DataFrame(rows)
    # Prefer tags that are flagged; if not enough, fill from highest magnitude.
    flagged_df = drift_df[drift_df["X_Drift_Flag"] == True].copy()  # noqa: E712
    if len(flagged_df) >= top_k:
        top_df = flagged_df.sort_values("Drift_Magnitude", ascending=False).head(top_k)
    else:
        remaining = top_k - len(flagged_df)
        filler_df = drift_df[drift_df["X_Drift_Flag"] != True].copy()  # noqa: E712
        filler_df = filler_df.sort_values("Drift_Magnitude", ascending=False).head(remaining)
        top_df = pd.concat([flagged_df, filler_df], ignore_index=True).sort_values(
            "Drift_Magnitude", ascending=False
        ).head(top_k)

    top_df = top_df.sort_values("Drift_Magnitude", ascending=False).reset_index(drop=True)
    return {
        "df": df,
        "top_tags_df": top_df,
        "target_drift_metrics": target_drift_metrics,
        "target_drift_time": target_drift_time,
        "historic_ratio": historic_ratio,
    }

