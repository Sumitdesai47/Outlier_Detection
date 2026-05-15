#!/usr/bin/env python3
"""
Auto Without-Causal-Matrix Outlier, Drift and Anomaly Detection

Purpose
-------
Detect tag-level outliers/drift/anomalies without using a causal matrix.

What this script auto-detects
-----------------------------
1. Excel sheet name, if not provided
2. Timestamp column
3. Long format vs wide format
4. Tag column and value column for long-format data
5. Numeric tag columns for wide-format data
6. Clean/reference baseline period using stable-data scoring
7. Clean-data mean/std or robust limits
8. Final status:
   - Normal
   - Drift
   - Drift + Anomaly
   - Strong Anomaly

Supported input formats
-----------------------
A) Long format:
   Timestamp | Tag | Actual_Value

B) Wide format:
   Timestamp | Tag_1 | Tag_2 | Tag_3 | ...

Basic run
---------
python auto_without_causal_outlier_drift.py --input-file data.xlsx

Optional run with manual override
---------------------------------
python auto_without_causal_outlier_drift.py \
    --input-file data.xlsx \
    --sheet-name All_Results \
    --timestamp-col Timestamp \
    --tag-col Tag \
    --value-col Actual_Value \
    --output-dir output_without_causal_auto

Outputs
-------
1. auto_without_causal_all_results.csv
2. auto_without_causal_outlier_drift_only.csv
3. auto_without_causal_clean_limits.csv
4. auto_without_causal_clean_reference_period.csv
5. auto_without_causal_timestamp_summary.csv
6. auto_without_causal_summary.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd


EPS = 1e-12


IGNORE_COL_KEYWORDS = [
    "final_class", "final status", "final_status", "status", "class",
    "predicted", "prediction", "residual", "zscore", "z_score",
    "lower", "upper", "limit", "threshold", "direction",
    "is_outlier", "outlier", "anomaly", "drift", "score",
    "clean", "flag", "remarks", "comment", "reason"
]

TIMESTAMP_NAME_HINTS = [
    "timestamp", "time_stamp", "datetime", "date_time", "time", "date"
]

TAG_NAME_HINTS = [
    "tag", "tags", "variable", "parameter", "sensor", "feature", "column", "point"
]

VALUE_NAME_HINTS = [
    "actual_value", "actual", "value", "reading", "measured_value",
    "measurement", "pv", "process_value"
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto-detect clean data and build without-causal outlier/drift/anomaly results."
    )

    parser.add_argument("--input-file", required=True, help="Input .xlsx, .xls, .csv, or .parquet file.")
    parser.add_argument("--sheet-name", default=None, help="Excel sheet name. If not given, best sheet is auto-detected.")
    parser.add_argument("--output-dir", default="without_causal_auto_output", help="Output folder.")
    parser.add_argument("--output-prefix", default="auto_without_causal", help="Output filename prefix.")

    parser.add_argument("--timestamp-col", default=None, help="Optional timestamp column override.")
    parser.add_argument("--tag-col", default=None, help="Optional tag column override for long data.")
    parser.add_argument("--value-col", default=None, help="Optional value column override for long data.")
    parser.add_argument("--tag-cols", default=None, help="Optional comma-separated tag columns for wide data.")

    parser.add_argument("--clean-window-fraction", type=float, default=0.15, help="Auto clean-period window size as fraction of rows. Default 0.15.")
    parser.add_argument("--min-clean-points", type=int, default=30, help="Minimum clean points per tag when possible. Default 30.")
    parser.add_argument("--clean-trim-quantile", type=float, default=0.85, help="Within auto clean period, keep rows with stability score <= this quantile. Default 0.85.")
    parser.add_argument("--max-clean-fraction", type=float, default=0.35, help="Maximum fallback clean fraction if contiguous window has too few rows. Default 0.35.")

    parser.add_argument("--baseline-method", choices=["std", "robust"], default="std", help="Clean limit method. std = mean/std, robust = median/MAD. Default std.")
    parser.add_argument("--drift-z", type=float, default=3.0, help="Z threshold for Drift. Default 3.0.")
    parser.add_argument("--drift-anomaly-z", type=float, default=3.5, help="Z threshold for Drift + Anomaly. Default 3.5.")
    parser.add_argument("--strong-anomaly-z", type=float, default=5.0, help="Z threshold for Strong Anomaly. Default 5.0.")

    parser.add_argument("--auto-thresholds", action="store_true", help="Estimate thresholds from clean-data score distribution, with safe minimums.")
    parser.add_argument("--max-rows", type=int, default=None, help="Optional max rows for very large files.")
    parser.add_argument("--datetime-format", default=None, help="Optional datetime format for faster parsing.")
    parser.add_argument("--save-wide-results", action="store_true", help="Also save a timestamp x tag final-class matrix.")

    return parser.parse_args()


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def lower_name(col: str) -> str:
    return str(col).strip().lower().replace("-", "_")


def safe_to_datetime(s: pd.Series, datetime_format: Optional[str] = None) -> pd.Series:
    if datetime_format:
        return pd.to_datetime(s, format=datetime_format, errors="coerce")
    return pd.to_datetime(s, errors="coerce")


def timestamp_score_for_col(df: pd.DataFrame, col: str, datetime_format: Optional[str] = None) -> float:
    name = lower_name(col)
    name_score = 0.0
    if any(h == name for h in TIMESTAMP_NAME_HINTS):
        name_score += 5.0
    elif any(h in name for h in TIMESTAMP_NAME_HINTS):
        name_score += 3.0

    sample = df[col].dropna().head(1000)
    if sample.empty:
        return name_score

    parsed = safe_to_datetime(sample, datetime_format=datetime_format)
    parse_ratio = parsed.notna().mean()
    unique_ratio = parsed.nunique() / max(len(parsed), 1)

    return name_score + parse_ratio * 5.0 + min(unique_ratio, 1.0) * 2.0


def detect_timestamp_col(df: pd.DataFrame, override: Optional[str] = None, datetime_format: Optional[str] = None) -> str:
    if override:
        if override not in df.columns:
            raise ValueError(f"Provided timestamp column not found: {override}")
        return override

    scores = [(col, timestamp_score_for_col(df, col, datetime_format=datetime_format)) for col in df.columns]
    scores = sorted(scores, key=lambda x: x[1], reverse=True)

    if not scores or scores[0][1] < 3.0:
        raise ValueError("Could not auto-detect timestamp column. Please provide --timestamp-col.")

    return scores[0][0]


def numeric_quality(series: pd.Series) -> float:
    converted = pd.to_numeric(series, errors="coerce")
    ratio = converted.notna().mean()
    unique = converted.nunique(dropna=True)
    return float(ratio) + min(unique / 20.0, 1.0)


def is_ignored_col(col: str) -> bool:
    name = lower_name(col)
    return any(k.replace(" ", "_") in name for k in IGNORE_COL_KEYWORDS)


def _count_wide_like_numeric_columns(df: pd.DataFrame, timestamp_col: str) -> int:
    """Columns that look like per-tag measurements in a wide matrix (same rule as detect_wide_tag_cols)."""
    n = 0
    for col in df.columns:
        if col == timestamp_col or is_ignored_col(col):
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().sum() >= 5 and converted.notna().mean() >= 0.2:
            n += 1
    return n


def detect_long_columns(
    df: pd.DataFrame,
    timestamp_col: str,
    tag_col_override: Optional[str],
    value_col_override: Optional[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    cols = [c for c in df.columns if c != timestamp_col]

    if tag_col_override and tag_col_override not in df.columns:
        raise ValueError(f"Provided tag column not found: {tag_col_override}")
    if value_col_override and value_col_override not in df.columns:
        raise ValueError(f"Provided value column not found: {value_col_override}")

    if tag_col_override and value_col_override:
        return True, tag_col_override, value_col_override

    # Wide exports (timestamp + many numeric tags) were sometimes misread as long when a
    # sparse numeric column paired with a *_Pv / *value* name. Prefer wide if enough tag-like columns exist.
    if _count_wide_like_numeric_columns(df, timestamp_col) >= 8:
        return False, None, None

    tag_candidates = []
    for col in cols:
        name = lower_name(col)
        if tag_col_override and col != tag_col_override:
            continue
        name_score = 4.0 if name in TAG_NAME_HINTS else 0.0
        name_score += 2.0 if any(h in name for h in TAG_NAME_HINTS) else 0.0
        as_text = df[col].astype(str).str.strip()
        unique_count = as_text.nunique(dropna=True)
        non_null_ratio = df[col].notna().mean()
        object_bonus = 1.0 if not pd.api.types.is_numeric_dtype(df[col]) else 0.0
        score = name_score + object_bonus + min(unique_count / 20.0, 2.0) + non_null_ratio
        tag_candidates.append((col, score, unique_count))

    value_candidates = []
    for col in cols:
        name = lower_name(col)
        if value_col_override and col != value_col_override:
            continue
        name_score = 4.0 if name in VALUE_NAME_HINTS else 0.0
        name_score += 2.0 if any(h in name for h in VALUE_NAME_HINTS) else 0.0
        score = name_score + numeric_quality(df[col])
        value_candidates.append((col, score))

    tag_candidates = sorted(tag_candidates, key=lambda x: x[1], reverse=True)
    value_candidates = sorted(value_candidates, key=lambda x: x[1], reverse=True)

    if not tag_candidates or not value_candidates:
        return False, None, None

    for tag_col, tag_score, tag_unique in tag_candidates:
        for value_col, value_score in value_candidates:
            if tag_col == value_col:
                continue
            if value_score < 1.2:
                continue
            if tag_col_override and tag_col != tag_col_override:
                continue
            if value_col_override and value_col != value_col_override:
                continue
            name_match = (
                any(h in lower_name(tag_col) for h in TAG_NAME_HINTS)
                or any(h in lower_name(value_col) for h in VALUE_NAME_HINTS)
            )
            enough_repetition = tag_unique < max(len(df) * 0.5, 20)
            if name_match or enough_repetition:
                return True, tag_col, value_col

    return False, None, None


def parse_tag_cols_argument(tag_cols: Optional[str]) -> Optional[list[str]]:
    if not tag_cols:
        return None
    return [c.strip() for c in tag_cols.split(",") if c.strip()]


def _read_excel_first_row_as_column_names(
    path: Path,
    sheet_name: str,
    max_data_rows: Optional[int],
) -> pd.DataFrame:
    """
    Treat the first physical row in the sheet as column headers (tag names + timestamp).

    Pandas default ``header=0`` can mis-handle some methodology/template workbooks
    (merged cells, odd layouts). Reading with ``header=None`` and promoting row 0
    keeps Excel row 1 as the definitive header row.
    """
    kwargs: dict = {"sheet_name": sheet_name, "header": None}
    if max_data_rows is not None:
        kwargs["nrows"] = int(max_data_rows) + 1
    raw = pd.read_excel(path, **kwargs)
    if raw.empty:
        return normalize_columns(raw)

    header_row = raw.iloc[0]
    new_cols: list[str] = []
    seen: dict[str, int] = {}
    for i, v in enumerate(header_row.tolist()):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            base = f"Column_{i}"
        else:
            base = str(v).strip()
            if not base or base.lower() == "nan":
                base = f"Column_{i}"
        col = base
        if col in seen:
            seen[col] += 1
            col = f"{base}_{seen[col]}"
        else:
            seen[col] = 0
        new_cols.append(col)

    df = raw.iloc[1:].copy()
    df.columns = new_cols
    df = df.reset_index(drop=True)
    if max_data_rows is not None and len(df) > int(max_data_rows):
        df = df.iloc[: int(max_data_rows)].reset_index(drop=True)
    return normalize_columns(df)


def detect_wide_tag_cols(df: pd.DataFrame, timestamp_col: str, provided_tag_cols: Optional[list[str]]) -> list[str]:
    if provided_tag_cols:
        missing = [c for c in provided_tag_cols if c not in df.columns]
        if missing:
            raise ValueError(f"Provided tag columns not found: {missing}")
        return provided_tag_cols

    tag_cols = []
    for col in df.columns:
        if col == timestamp_col or is_ignored_col(col):
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().sum() >= 5 and converted.notna().mean() >= 0.2:
            tag_cols.append(col)

    if not tag_cols:
        raise ValueError("Could not auto-detect numeric tag columns. Provide --tag-cols or --tag-col/--value-col.")
    return tag_cols


def excel_sheet_score(path: Path, sheet_name: str, datetime_format: Optional[str]) -> float:
    try:
        df = _read_excel_first_row_as_column_names(path, sheet_name, 500)
    except Exception:
        return -1.0
    if df.empty:
        return -1.0
    df = normalize_columns(df)

    score = 0.0
    try:
        ts = detect_timestamp_col(df, datetime_format=datetime_format)
        score += 5.0
        is_long, tag_col, value_col = detect_long_columns(df, ts, None, None)
        if is_long:
            score += 4.0
        else:
            wide_cols = detect_wide_tag_cols(df, ts, None)
            score += min(len(wide_cols), 10) / 2.0
    except Exception:
        pass

    useful_numeric = 0
    for col in df.columns:
        if is_ignored_col(col):
            continue
        useful_numeric += int(pd.to_numeric(df[col], errors="coerce").notna().sum() > 10)
    score += min(useful_numeric, 10) * 0.2
    return score


def read_input_file(
    path: str,
    sheet_name: Optional[str],
    max_rows: Optional[int],
    datetime_format: Optional[str],
) -> Tuple[pd.DataFrame, Optional[str]]:
    path_obj = Path(path)
    if not path_obj.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    suffix = path_obj.suffix.lower()
    selected_sheet = sheet_name

    if suffix in [".xlsx", ".xls"]:
        if selected_sheet is None:
            xl = pd.ExcelFile(path_obj)

            preferred_sheet_names = [
                "all_results", "all result", "allresults",
                "results", "result", "data", "raw_data", "raw data",
                "timeseries", "time_series", "time series"
            ]

            lower_to_original = {str(s).strip().lower(): s for s in xl.sheet_names}
            for preferred in preferred_sheet_names:
                if preferred in lower_to_original:
                    selected_sheet = lower_to_original[preferred]
                    break

            if selected_sheet is None:
                sheet_scores = [(s, excel_sheet_score(path_obj, s, datetime_format=datetime_format)) for s in xl.sheet_names]
                sheet_scores = sorted(sheet_scores, key=lambda x: x[1], reverse=True)
                if not sheet_scores or sheet_scores[0][1] < 1:
                    selected_sheet = xl.sheet_names[0]
                else:
                    selected_sheet = sheet_scores[0][0]

        df = _read_excel_first_row_as_column_names(path_obj, selected_sheet, max_rows)
    elif suffix == ".csv":
        df = pd.read_csv(path_obj, nrows=max_rows)
    elif suffix == ".parquet":
        df = pd.read_parquet(path_obj)
        if max_rows:
            df = df.head(max_rows)
    else:
        raise ValueError("Supported files are .xlsx, .xls, .csv, and .parquet")

    df = normalize_columns(df)
    return df, selected_sheet


def make_long_format(
    df: pd.DataFrame,
    timestamp_col: str,
    tag_col: Optional[str],
    value_col: Optional[str],
    tag_cols: Optional[list[str]],
    datetime_format: Optional[str],
) -> Tuple[pd.DataFrame, str, str, str, str]:
    is_long, detected_tag_col, detected_value_col = detect_long_columns(df, timestamp_col, tag_col, value_col)

    if is_long:
        final_tag_col = detected_tag_col
        final_value_col = detected_value_col
        long_df = df[[timestamp_col, final_tag_col, final_value_col]].copy()
        long_df.columns = ["Timestamp", "Tag", "Actual_Value"]
        input_format = "long"
    else:
        wide_tag_cols = detect_wide_tag_cols(df, timestamp_col, tag_cols)
        long_df = df[[timestamp_col] + wide_tag_cols].melt(
            id_vars=[timestamp_col],
            value_vars=wide_tag_cols,
            var_name="Tag",
            value_name="Actual_Value",
        )
        long_df = long_df.rename(columns={timestamp_col: "Timestamp"})
        final_tag_col = "AUTO_WIDE_COLUMNS"
        final_value_col = "AUTO_WIDE_VALUES"
        input_format = "wide"

    long_df["Timestamp"] = safe_to_datetime(long_df["Timestamp"], datetime_format=datetime_format)
    long_df["Tag"] = long_df["Tag"].astype(str).str.strip()
    long_df["Actual_Value"] = pd.to_numeric(long_df["Actual_Value"], errors="coerce")

    long_df = long_df.dropna(subset=["Timestamp", "Tag", "Actual_Value"])
    long_df = long_df[long_df["Tag"].str.lower().ne("nan")]
    long_df = long_df.sort_values(["Timestamp", "Tag"]).reset_index(drop=True)

    if long_df.empty:
        raise ValueError("No valid rows after converting to Timestamp + Tag + Actual_Value format.")

    return long_df, input_format, timestamp_col, final_tag_col, final_value_col


def robust_scale(values: pd.Series) -> float:
    arr = pd.to_numeric(values, errors="coerce").dropna().astype(float)
    if arr.empty:
        return np.nan

    med = np.nanmedian(arr)
    mad = np.nanmedian(np.abs(arr - med)) * 1.4826
    if np.isfinite(mad) and mad > EPS:
        return float(mad)

    std = np.nanstd(arr, ddof=1)
    if np.isfinite(std) and std > EPS:
        return float(std)

    q75, q25 = np.nanpercentile(arr, [75, 25])
    iqr_scale = (q75 - q25) / 1.349
    if np.isfinite(iqr_scale) and iqr_scale > EPS:
        return float(iqr_scale)

    return EPS


def build_pivot(long_df: pd.DataFrame) -> pd.DataFrame:
    pivot = long_df.pivot_table(
        index="Timestamp",
        columns="Tag",
        values="Actual_Value",
        aggfunc="mean"
    ).sort_index()
    pivot = pivot.dropna(axis=1, how="all")
    return pivot


def calculate_stability_score(pivot: pd.DataFrame) -> pd.Series:
    med = pivot.median(axis=0, skipna=True)
    scale = pivot.apply(robust_scale, axis=0).replace(0, EPS)

    z = (pivot - med) / scale
    abs_z = z.abs().clip(upper=10)

    level_score = abs_z.median(axis=1, skipna=True)

    dz = z.diff().abs().clip(upper=10)
    change_score = dz.median(axis=1, skipna=True)
    change_score = change_score.fillna(change_score.median())

    level_score = level_score.fillna(level_score.median())
    change_score = change_score.fillna(change_score.median())

    score = 0.75 * level_score + 0.25 * change_score
    score = score.replace([np.inf, -np.inf], np.nan)
    score = score.fillna(score.median())

    return score


def auto_detect_clean_timestamps(
    pivot: pd.DataFrame,
    clean_window_fraction: float,
    min_clean_points: int,
    clean_trim_quantile: float,
    max_clean_fraction: float,
) -> Tuple[pd.Index, pd.DataFrame]:
    n = len(pivot)
    if n == 0:
        raise ValueError("Cannot detect clean period because there are no timestamps.")

    score = calculate_stability_score(pivot)

    if n <= min_clean_points:
        selected = score.index
        diagnostic = pd.DataFrame({
            "Timestamp": score.index,
            "Stability_Score": score.values,
            "Clean_Selected": True,
            "Clean_Detection_Method": "all_rows_due_to_small_data",
        })
        return selected, diagnostic

    auto_window = int(round(n * clean_window_fraction))
    auto_window = max(auto_window, min_clean_points)
    auto_window = min(auto_window, max(min(int(n * 0.50), n), min_clean_points))
    auto_window = min(auto_window, n)

    rolling_score = score.rolling(window=auto_window, min_periods=max(3, int(auto_window * 0.7))).mean()
    valid_rolling = rolling_score.dropna()

    if valid_rolling.empty:
        fallback_count = min(max(min_clean_points, int(n * max_clean_fraction)), n)
        selected = score.nsmallest(fallback_count).index.sort_values()
        selected_set = set(selected)
        diagnostic = pd.DataFrame({
            "Timestamp": score.index,
            "Stability_Score": score.values,
            "Rolling_Stability_Score": rolling_score.reindex(score.index).values,
            "Clean_Selected": [ts in selected_set for ts in score.index],
            "Clean_Detection_Method": "lowest_stability_rows_fallback",
        })
        return selected, diagnostic

    best_end = valid_rolling.idxmin()

    best_end_pos = score.index.get_loc(best_end)
    best_start_pos = max(0, best_end_pos - auto_window + 1)
    candidate_idx = score.index[best_start_pos:best_end_pos + 1]
    candidate_score = score.loc[candidate_idx]

    trim_limit = candidate_score.quantile(clean_trim_quantile)
    selected = candidate_score[candidate_score <= trim_limit].index

    if len(selected) < min_clean_points:
        selected = candidate_idx

    if len(selected) < min_clean_points:
        fallback_count = min(max(min_clean_points, int(n * max_clean_fraction)), n)
        selected = score.nsmallest(fallback_count).index.sort_values()

    selected_set = set(selected)
    diagnostic = pd.DataFrame({
        "Timestamp": score.index,
        "Stability_Score": score.values,
        "Rolling_Stability_Score": rolling_score.reindex(score.index).values,
        "Clean_Selected": [ts in selected_set for ts in score.index],
        "Clean_Detection_Method": "lowest_stability_contiguous_window",
    })

    return selected.sort_values(), diagnostic


def maybe_auto_thresholds(
    clean_df: pd.DataFrame,
    limits_df: pd.DataFrame,
    drift_z: float,
    drift_anomaly_z: float,
    strong_anomaly_z: float,
    enabled: bool,
) -> Tuple[float, float, float]:
    if not enabled:
        return drift_z, drift_anomaly_z, strong_anomaly_z

    merged = clean_df.merge(
        limits_df[["Tag", "Baseline_Center", "Baseline_Scale"]],
        on="Tag",
        how="left",
    )
    merged["Abs_Z"] = ((merged["Actual_Value"] - merged["Baseline_Center"]) / merged["Baseline_Scale"]).abs()
    vals = merged["Abs_Z"].replace([np.inf, -np.inf], np.nan).dropna()

    if vals.empty:
        return drift_z, drift_anomaly_z, strong_anomaly_z

    auto_drift = max(3.0, float(vals.quantile(0.995)))
    auto_drift_anomaly = max(auto_drift + 0.5, float(vals.quantile(0.999)))
    auto_strong = max(auto_drift_anomaly + 1.0, float(vals.quantile(0.9995)))

    auto_drift = round(auto_drift, 3)
    auto_drift_anomaly = round(auto_drift_anomaly, 3)
    auto_strong = round(auto_strong, 3)

    return auto_drift, auto_drift_anomaly, auto_strong


def calculate_clean_limits(
    long_df: pd.DataFrame,
    clean_timestamps: pd.Index,
    baseline_method: str,
    min_clean_points: int,
) -> pd.DataFrame:
    clean_set = set(clean_timestamps)
    clean_df = long_df[long_df["Timestamp"].isin(clean_set)].copy()

    rows = []
    global_by_tag = long_df.groupby("Tag")

    for tag, g in global_by_tag:
        clean_g = clean_df[clean_df["Tag"] == tag]
        baseline_g = clean_g

        fallback_used = False
        if len(baseline_g) < max(5, min_clean_points // 3):
            fallback_used = True
            baseline_g = g.copy()

        vals = baseline_g["Actual_Value"].dropna().astype(float)

        if vals.empty:
            continue

        if baseline_method == "robust":
            center = float(np.nanmedian(vals))
            scale = robust_scale(vals)
            center_type = "median"
            scale_type = "MAD_scaled"
        else:
            center = float(vals.mean())
            scale = float(vals.std(ddof=1))
            center_type = "mean"
            scale_type = "std"
            if not np.isfinite(scale) or scale <= EPS:
                scale = robust_scale(vals)
                scale_type = "fallback_robust_scale"

        if not np.isfinite(scale) or scale <= EPS:
            scale = EPS

        rows.append({
            "Tag": tag,
            "Baseline_Method": baseline_method,
            "Baseline_Center": center,
            "Baseline_Scale": scale,
            "Center_Type": center_type,
            "Scale_Type": scale_type,
            "Clean_Count": int(len(clean_g)),
            "Baseline_Count_Used": int(len(vals)),
            "Fallback_Global_Baseline_Used": bool(fallback_used),
            "Clean_Min": float(clean_g["Actual_Value"].min()) if len(clean_g) else np.nan,
            "Clean_Max": float(clean_g["Actual_Value"].max()) if len(clean_g) else np.nan,
        })

    limits_df = pd.DataFrame(rows).sort_values("Tag").reset_index(drop=True)
    if limits_df.empty:
        raise ValueError("Could not calculate clean limits. No valid tag data found.")

    return limits_df


def add_threshold_columns(limits_df: pd.DataFrame, drift_z: float, drift_anomaly_z: float, strong_z: float) -> pd.DataFrame:
    limits_df = limits_df.copy()
    limits_df["Drift_Z"] = drift_z
    limits_df["Drift_Anomaly_Z"] = drift_anomaly_z
    limits_df["Strong_Anomaly_Z"] = strong_z

    limits_df["Drift_Lower_Limit"] = limits_df["Baseline_Center"] - drift_z * limits_df["Baseline_Scale"]
    limits_df["Drift_Upper_Limit"] = limits_df["Baseline_Center"] + drift_z * limits_df["Baseline_Scale"]

    limits_df["Drift_Anomaly_Lower_Limit"] = limits_df["Baseline_Center"] - drift_anomaly_z * limits_df["Baseline_Scale"]
    limits_df["Drift_Anomaly_Upper_Limit"] = limits_df["Baseline_Center"] + drift_anomaly_z * limits_df["Baseline_Scale"]

    limits_df["Strong_Anomaly_Lower_Limit"] = limits_df["Baseline_Center"] - strong_z * limits_df["Baseline_Scale"]
    limits_df["Strong_Anomaly_Upper_Limit"] = limits_df["Baseline_Center"] + strong_z * limits_df["Baseline_Scale"]

    return limits_df


def classify_results(
    long_df: pd.DataFrame,
    limits_df: pd.DataFrame,
    clean_timestamps: pd.Index,
    clean_period_start: pd.Timestamp,
    clean_period_end: pd.Timestamp,
) -> pd.DataFrame:
    result = long_df.merge(limits_df, on="Tag", how="left")

    result["Value_Z"] = (result["Actual_Value"] - result["Baseline_Center"]) / result["Baseline_Scale"]
    result["Abs_Z"] = result["Value_Z"].abs()

    drift_z = result["Drift_Z"]
    drift_anomaly_z = result["Drift_Anomaly_Z"]
    strong_z = result["Strong_Anomaly_Z"]

    result["Final_Class"] = np.select(
        [
            result["Abs_Z"] >= strong_z,
            result["Abs_Z"] >= drift_anomaly_z,
            result["Abs_Z"] >= drift_z,
        ],
        [
            "Strong Anomaly",
            "Drift + Anomaly",
            "Drift",
        ],
        default="Normal",
    )

    result["Final_Status"] = np.where(result["Final_Class"].eq("Normal"), "Normal", "Abnormal")
    result["Direction"] = np.select(
        [
            result["Final_Status"].eq("Normal"),
            result["Value_Z"] > 0,
            result["Value_Z"] < 0,
        ],
        [
            "NORMAL",
            "UP",
            "DOWN",
        ],
        default="UNKNOWN",
    )

    result["Limit_Crossed"] = np.select(
        [
            result["Final_Class"].eq("Strong Anomaly") & result["Direction"].eq("UP"),
            result["Final_Class"].eq("Strong Anomaly") & result["Direction"].eq("DOWN"),
            result["Final_Class"].eq("Drift + Anomaly") & result["Direction"].eq("UP"),
            result["Final_Class"].eq("Drift + Anomaly") & result["Direction"].eq("DOWN"),
            result["Final_Class"].eq("Drift") & result["Direction"].eq("UP"),
            result["Final_Class"].eq("Drift") & result["Direction"].eq("DOWN"),
        ],
        [
            "Strong_Anomaly_Upper_Limit",
            "Strong_Anomaly_Lower_Limit",
            "Drift_Anomaly_Upper_Limit",
            "Drift_Anomaly_Lower_Limit",
            "Drift_Upper_Limit",
            "Drift_Lower_Limit",
        ],
        default="Within_Limits",
    )

    clean_set = set(clean_timestamps)
    result["Auto_Clean_Reference_Row"] = result["Timestamp"].isin(clean_set)
    result["Auto_Clean_Period_Start"] = clean_period_start
    result["Auto_Clean_Period_End"] = clean_period_end

    ordered_cols = [
        "Timestamp", "Tag", "Actual_Value",
        "Final_Class", "Final_Status", "Direction", "Limit_Crossed",
        "Value_Z", "Abs_Z",
        "Auto_Clean_Reference_Row", "Auto_Clean_Period_Start", "Auto_Clean_Period_End",
        "Baseline_Method", "Baseline_Center", "Baseline_Scale",
        "Clean_Count", "Baseline_Count_Used", "Fallback_Global_Baseline_Used",
        "Drift_Z", "Drift_Lower_Limit", "Drift_Upper_Limit",
        "Drift_Anomaly_Z", "Drift_Anomaly_Lower_Limit", "Drift_Anomaly_Upper_Limit",
        "Strong_Anomaly_Z", "Strong_Anomaly_Lower_Limit", "Strong_Anomaly_Upper_Limit",
    ]

    existing_ordered = [c for c in ordered_cols if c in result.columns]
    extra_cols = [c for c in result.columns if c not in existing_ordered]
    result = result[existing_ordered + extra_cols]

    return result.sort_values(["Timestamp", "Tag"]).reset_index(drop=True)


def build_timestamp_summary(result: pd.DataFrame) -> pd.DataFrame:
    summary = result.groupby("Timestamp").agg(
        Total_Tags=("Tag", "nunique"),
        Abnormal_Tag_Count=("Final_Status", lambda s: int((s == "Abnormal").sum())),
        Drift_Count=("Final_Class", lambda s: int((s == "Drift").sum())),
        Drift_Anomaly_Count=("Final_Class", lambda s: int((s == "Drift + Anomaly").sum())),
        Strong_Anomaly_Count=("Final_Class", lambda s: int((s == "Strong Anomaly").sum())),
        Max_Abs_Z=("Abs_Z", "max"),
        Mean_Abs_Z=("Abs_Z", "mean"),
    ).reset_index()

    summary["Timestamp_Status"] = np.where(summary["Abnormal_Tag_Count"] > 0, "Abnormal", "Normal")
    return summary


def build_summary(
    result: pd.DataFrame,
    limits_df: pd.DataFrame,
    clean_diag: pd.DataFrame,
    selected_sheet: Optional[str],
    input_format: str,
    detected_timestamp_col: str,
    detected_tag_col: str,
    detected_value_col: str,
    thresholds: Tuple[float, float, float],
) -> pd.DataFrame:
    total_rows = len(result)
    abnormal_rows = int((result["Final_Status"] == "Abnormal").sum())
    normal_rows = int((result["Final_Status"] == "Normal").sum())

    counts = result["Final_Class"].value_counts().to_dict()

    clean_selected_count = int(clean_diag["Clean_Selected"].sum())
    clean_start = clean_diag.loc[clean_diag["Clean_Selected"], "Timestamp"].min()
    clean_end = clean_diag.loc[clean_diag["Clean_Selected"], "Timestamp"].max()

    rows = [
        ("Selected_Excel_Sheet", selected_sheet if selected_sheet is not None else ""),
        ("Input_Format_Detected", input_format),
        ("Detected_Timestamp_Column", detected_timestamp_col),
        ("Detected_Tag_Column", detected_tag_col),
        ("Detected_Value_Column", detected_value_col),
        ("Total_Result_Rows", total_rows),
        ("Total_Tags", int(result["Tag"].nunique())),
        ("Total_Timestamps", int(result["Timestamp"].nunique())),
        ("Auto_Clean_Selected_Timestamps", clean_selected_count),
        ("Auto_Clean_Period_Start", clean_start),
        ("Auto_Clean_Period_End", clean_end),
        ("Normal_Rows", normal_rows),
        ("Abnormal_Rows", abnormal_rows),
        ("Abnormal_Rate", abnormal_rows / total_rows if total_rows else np.nan),
        ("Drift_Count", int(counts.get("Drift", 0))),
        ("Drift_Anomaly_Count", int(counts.get("Drift + Anomaly", 0))),
        ("Strong_Anomaly_Count", int(counts.get("Strong Anomaly", 0))),
        ("Drift_Z", thresholds[0]),
        ("Drift_Anomaly_Z", thresholds[1]),
        ("Strong_Anomaly_Z", thresholds[2]),
        ("Tags_With_Global_Fallback_Baseline", int(limits_df["Fallback_Global_Baseline_Used"].sum())),
    ]

    return pd.DataFrame(rows, columns=["Metric", "Value"])


def save_outputs(
    result: pd.DataFrame,
    limits_df: pd.DataFrame,
    clean_diag: pd.DataFrame,
    timestamp_summary: pd.DataFrame,
    summary_df: pd.DataFrame,
    output_dir: str,
    output_prefix: str,
    save_wide_results: bool,
) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths = {
        "all_results": out / f"{output_prefix}_all_results.csv",
        "outlier_drift_only": out / f"{output_prefix}_outlier_drift_only.csv",
        "clean_limits": out / f"{output_prefix}_clean_limits.csv",
        "clean_reference_period": out / f"{output_prefix}_clean_reference_period.csv",
        "timestamp_summary": out / f"{output_prefix}_timestamp_summary.csv",
        "summary": out / f"{output_prefix}_summary.csv",
    }

    result.to_csv(paths["all_results"], index=False)
    result[result["Final_Status"] == "Abnormal"].to_csv(paths["outlier_drift_only"], index=False)
    limits_df.to_csv(paths["clean_limits"], index=False)
    clean_diag.to_csv(paths["clean_reference_period"], index=False)
    timestamp_summary.to_csv(paths["timestamp_summary"], index=False)
    summary_df.to_csv(paths["summary"], index=False)

    if save_wide_results:
        wide_class = result.pivot_table(
            index="Timestamp",
            columns="Tag",
            values="Final_Class",
            aggfunc="first",
        ).reset_index()
        paths["wide_final_class"] = out / f"{output_prefix}_wide_final_class.csv"
        wide_class.to_csv(paths["wide_final_class"], index=False)

    return {k: str(v) for k, v in paths.items()}


def print_completion(summary_df: pd.DataFrame, paths: dict) -> None:
    print("\nAUTO WITHOUT-CAUSAL OUTLIER + DRIFT DETECTION COMPLETED\n")
    print(summary_df.to_string(index=False))
    print("\nSaved files:")
    for key, path in paths.items():
        print(f"- {key}: {path}")


def main() -> None:
    args = parse_args()

    if not (args.drift_z < args.drift_anomaly_z < args.strong_anomaly_z):
        raise ValueError("Thresholds must satisfy: drift-z < drift-anomaly-z < strong-anomaly-z")

    raw_df, selected_sheet = read_input_file(
        args.input_file,
        sheet_name=args.sheet_name,
        max_rows=args.max_rows,
        datetime_format=args.datetime_format,
    )

    timestamp_col = detect_timestamp_col(
        raw_df,
        override=args.timestamp_col,
        datetime_format=args.datetime_format,
    )

    tag_cols = parse_tag_cols_argument(args.tag_cols)

    long_df, input_format, detected_ts_col, detected_tag_col, detected_value_col = make_long_format(
        raw_df,
        timestamp_col=timestamp_col,
        tag_col=args.tag_col,
        value_col=args.value_col,
        tag_cols=tag_cols,
        datetime_format=args.datetime_format,
    )

    pivot = build_pivot(long_df)

    clean_timestamps, clean_diag = auto_detect_clean_timestamps(
        pivot,
        clean_window_fraction=args.clean_window_fraction,
        min_clean_points=args.min_clean_points,
        clean_trim_quantile=args.clean_trim_quantile,
        max_clean_fraction=args.max_clean_fraction,
    )

    clean_start = pd.Series(clean_timestamps).min()
    clean_end = pd.Series(clean_timestamps).max()

    limits_df = calculate_clean_limits(
        long_df,
        clean_timestamps=clean_timestamps,
        baseline_method=args.baseline_method,
        min_clean_points=args.min_clean_points,
    )

    clean_df = long_df[long_df["Timestamp"].isin(set(clean_timestamps))].copy()
    drift_z, drift_anomaly_z, strong_z = maybe_auto_thresholds(
        clean_df,
        limits_df,
        drift_z=args.drift_z,
        drift_anomaly_z=args.drift_anomaly_z,
        strong_anomaly_z=args.strong_anomaly_z,
        enabled=args.auto_thresholds,
    )

    limits_df = add_threshold_columns(
        limits_df,
        drift_z=drift_z,
        drift_anomaly_z=drift_anomaly_z,
        strong_z=strong_z,
    )

    result = classify_results(
        long_df,
        limits_df,
        clean_timestamps=clean_timestamps,
        clean_period_start=clean_start,
        clean_period_end=clean_end,
    )

    timestamp_summary = build_timestamp_summary(result)

    summary_df = build_summary(
        result=result,
        limits_df=limits_df,
        clean_diag=clean_diag,
        selected_sheet=selected_sheet,
        input_format=input_format,
        detected_timestamp_col=detected_ts_col,
        detected_tag_col=detected_tag_col,
        detected_value_col=detected_value_col,
        thresholds=(drift_z, drift_anomaly_z, strong_z),
    )

    paths = save_outputs(
        result=result,
        limits_df=limits_df,
        clean_diag=clean_diag,
        timestamp_summary=timestamp_summary,
        summary_df=summary_df,
        output_dir=args.output_dir,
        output_prefix=args.output_prefix,
        save_wide_results=args.save_wide_results,
    )

    print_completion(summary_df, paths)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
