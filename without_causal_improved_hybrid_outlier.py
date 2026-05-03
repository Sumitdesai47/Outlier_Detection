"""
Improved hybrid without-causal baselines for Auto Identification (part6).

Blends clean-period mean/median centers and scales from std, MAD, and IQR so
per-tag adaptive z-bands are anchored on a scale that is not underestimated
when the clean window has mild heavy tails or outliers.
"""

from __future__ import annotations

import pandas as pd


def enhance_limits_hybrid_robust(limits_df: pd.DataFrame, clean_df: pd.DataFrame) -> pd.DataFrame:
    """
    Update Baseline_Center / Baseline_Scale per tag using hybrid robust stats
    from clean_df (long: Timestamp, Tag, Actual_Value).

    Scale uses max(script std scale, Gaussian MAD, Gaussian IQR) with a small
    floor. Center blends the existing limit center (mean-based from upstream)
    with the clean median.
    """
    out = limits_df.copy()
    if out.empty or clean_df.empty:
        return out

    value_col = "Actual_Value"
    if value_col not in clean_df.columns or "Tag" not in out.columns:
        return out

    for i, row in out.iterrows():
        tag = row.get("Tag")
        if tag is None:
            continue
        y = pd.to_numeric(
            clean_df.loc[clean_df["Tag"] == tag, value_col], errors="coerce"
        ).dropna()
        if len(y) < 3:
            continue

        med = float(y.median())
        std_s = float(y.std(ddof=1) or 0.0)

        dev = (y - med).abs()
        mad = float(dev.median() or 0.0)
        if mad < 1e-12:
            mad_sigma = std_s
        else:
            mad_sigma = 1.4826 * mad

        q1, q3 = y.quantile(0.25), y.quantile(0.75)
        iqr = float(q3 - q1)
        iqr_sigma = (iqr / 1.349) if iqr > 1e-12 else 0.0

        orig_center = float(row.get("Baseline_Center") or med)
        orig_scale = float(row.get("Baseline_Scale") or 0.0)

        scale_h = max(orig_scale, mad_sigma, iqr_sigma, std_s, 1e-9)
        center_h = 0.5 * orig_center + 0.5 * med

        out.at[i, "Baseline_Center"] = center_h
        out.at[i, "Baseline_Scale"] = scale_h

    return out
