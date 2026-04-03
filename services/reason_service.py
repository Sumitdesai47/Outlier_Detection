from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .time_series_utils import format_date_us_mdy


def _fmt_float(x: Any, ndigits: int = 4) -> str:
    if x is None:
        return "NA"
    try:
        if isinstance(x, float) and (np.isnan(x) or np.isinf(x)):
            return "NA"
        return f"{float(x):.{ndigits}f}"
    except Exception:
        return str(x)


def _fmt_ts(x: Any) -> str:
    if x is None:
        return "NA"
    try:
        if pd.isna(x):
            return "NA"
        return format_date_us_mdy(x)
    except Exception:
        return str(x)


def build_top_causes_with_reasons(
    all_scores_df: pd.DataFrame,
    top_root_df: pd.DataFrame,
    *,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """
    Turn Pipeline.py rows into a user-facing list:
      - cause tag
      - numeric evidence fields
      - a human readable reason string
    """
    if top_root_df.empty:
        return []

    top_df = top_root_df.head(top_n).copy()
    # Ensure expected columns exist (Pipeline produces them, but guard anyway).
    cols = set(top_df.columns)

    out: List[Dict[str, Any]] = []
    for _, row in top_df.iterrows():
        x_tag = row.get("X_Tag", "")
        cause_type = row.get("Cause_Type", "")

        drift_flag = bool(row.get("X_Drift_Flag", False))
        drift_time = row.get("X_Drift_Time", None)

        ks_stat = row.get("KS_Stat", np.nan)
        p_value = row.get("Granger_p_value", row.get("p_value", np.nan))
        mean_shift = row.get("Mean_Shift", np.nan)
        std_shift = row.get("Std_Shift", np.nan)

        granger_score = row.get("Granger_Score", np.nan)
        spearman_corr = row.get("Spearman_Corr", np.nan)

        best_lag = row.get("Best_Lead_Lag", np.nan)
        lag_corr = row.get("Lag_Correlation", np.nan)
        drift_lead_score = row.get("Drift_Lead_Score", np.nan)

        base_shap = row.get("Base_SHAP", np.nan)

        reason_parts = []
        reason_parts.append(
            f"Drift evidence: drift_flag={drift_flag}, first_drift={_fmt_ts(drift_time)}, "
            f"KS_stat={_fmt_float(ks_stat)}, mean_shift={_fmt_float(mean_shift)}, std_shift={_fmt_float(std_shift)}."
        )
        if not pd.isna(granger_score):
            reason_parts.append(
                f"Causal/lead evidence: Granger_score={_fmt_float(granger_score)}, Spearman_corr={_fmt_float(spearman_corr)}, "
                f"best_lead_lag={_fmt_float(best_lag)}, lag_corr={_fmt_float(lag_corr)}, drift_lead_score={_fmt_float(drift_lead_score)}."
            )
        if not pd.isna(base_shap):
            reason_parts.append(f"Model impact: SHAP(Base_Tag)={_fmt_float(base_shap)}.")

        matched_paths = row.get("Matched_Paths", "")
        if isinstance(matched_paths, str) and matched_paths.strip():
            reason_parts.append(f"Path matches: {matched_paths}")

        out.append(
            {
                "cause_tag": x_tag,
                "cause_type": cause_type,
                "root_cause_score": _fmt_float(row.get("Root_Cause_Score", np.nan), 5),
                "direct_cause_score": _fmt_float(row.get("Direct_Cause_Score", np.nan), 5),
                "indirect_cause_score": _fmt_float(row.get("Indirect_Cause_Score", np.nan), 5),
                "drift_flag": drift_flag,
                "x_drift_time": _fmt_ts(drift_time),
                "ks_stat": _fmt_float(ks_stat),
                "mean_shift": _fmt_float(mean_shift),
                "std_shift": _fmt_float(std_shift),
                "granger_score": _fmt_float(granger_score),
                "spearman_corr": _fmt_float(spearman_corr),
                "best_lead_lag": _fmt_float(best_lag),
                "lag_correlation": _fmt_float(lag_corr),
                "base_shap": _fmt_float(base_shap),
                "reason": " ".join([p for p in reason_parts if p]),
            }
        )

    return out

