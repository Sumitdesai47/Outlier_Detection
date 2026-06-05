"""Safe correlation / scaling helpers (avoid divide-by-zero on constant columns)."""
from __future__ import annotations

import warnings
from typing import List, Sequence

import numpy as np
import pandas as pd

_MIN_STD = 1e-12


def safe_series_corr(
    a: pd.Series,
    b: pd.Series,
    *,
    method: str = "pearson",
) -> float:
    """Pearson or Spearman correlation; returns 0.0 when variance is zero or n is too small."""
    x = pd.to_numeric(a, errors="coerce")
    y = pd.to_numeric(b, errors="coerce")
    mask = x.notna() & y.notna()
    if int(mask.sum()) < 3:
        return 0.0
    x = x.loc[mask]
    y = y.loc[mask]
    if str(method).lower().startswith("s"):
        x = x.rank()
        y = y.rank()
    sx = float(x.std(ddof=1))
    sy = float(y.std(ddof=1))
    if sx < _MIN_STD or sy < _MIN_STD:
        return 0.0
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        r = x.corr(y, method="pearson")
    if r is None or not np.isfinite(r):
        return 0.0
    return float(r)


def safe_corr_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Correlation matrix without RuntimeWarning on constant / missing columns."""
    cols = [str(c) for c in df.columns]
    n = len(cols)
    if n == 0:
        return pd.DataFrame()
    if n == 1:
        return pd.DataFrame([[1.0]], index=cols, columns=cols)

    sub = df[cols].apply(pd.to_numeric, errors="coerce")
    mat = np.eye(n, dtype=float)
    for i in range(n):
        for j in range(i + 1, n):
            r = safe_series_corr(sub.iloc[:, i], sub.iloc[:, j], method="pearson")
            mat[i, j] = mat[j, i] = r
    return pd.DataFrame(mat, index=cols, columns=cols)


def non_constant_columns(df: pd.DataFrame, cols: Sequence[str]) -> List[str]:
    """Column names with sample std above threshold."""
    keep: List[str] = []
    for c in cols:
        s = pd.to_numeric(df[c], errors="coerce")
        if float(s.std(ddof=1)) >= _MIN_STD:
            keep.append(str(c))
    return keep
