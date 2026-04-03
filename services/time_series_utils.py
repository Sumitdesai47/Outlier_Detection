import numpy as np
import pandas as pd


def format_date_us_mdy(x) -> str:
    """
    Display date as US style without leading zeros: 5/3/2022.
    If the value has a non-midnight time, append HH:MM:SS.
    """
    if x is None:
        return "NA"
    try:
        if isinstance(x, float) and pd.isna(x):
            return "NA"
        t = pd.to_datetime(x, errors="coerce", dayfirst=False)
        if pd.isna(t):
            return str(x)
        d = f"{int(t.month)}/{int(t.day)}/{int(t.year)}"
        if t.hour != 0 or t.minute != 0 or t.second != 0 or t.microsecond != 0:
            return f"{d} {t.hour:02d}:{t.minute:02d}:{t.second:02d}"
        return d
    except Exception:
        return str(x)


def safe_parse_datetime_series(series: pd.Series) -> pd.Series:
    """
    Safely parse to datetime without accidental epoch conversion.
    - Keep datetime dtype as-is.
    - Parse strings/object with US order.
    - For mostly numeric ambiguous data, return NaT (unless they clearly look
      like known timestamp units).
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return series

    s = series.copy()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    s_num = pd.to_numeric(s, errors="coerce")
    num_mask = s_num.notna()

    # Parse non-numeric values as regular datetime strings (US order).
    non_num_mask = ~num_mask
    if non_num_mask.any():
        out.loc[non_num_mask] = pd.to_datetime(s.loc[non_num_mask], errors="coerce", dayfirst=False)

    # Parse numeric subset only when values clearly match known timestamp encodings.
    if num_mask.any():
        num = s_num.loc[num_mask]
        excel_mask = (num >= 20000) & (num <= 80000)
        sec_mask = (num >= 1e9) & (num <= 2e10)
        ms_mask = (num >= 1e12) & (num <= 2e13)

        if excel_mask.any():
            out.loc[num.index[excel_mask]] = pd.to_datetime(
                num.loc[excel_mask], unit="D", origin="1899-12-30", errors="coerce"
            )
        if sec_mask.any():
            out.loc[num.index[sec_mask]] = pd.to_datetime(num.loc[sec_mask], unit="s", errors="coerce")
        if ms_mask.any():
            out.loc[num.index[ms_mask]] = pd.to_datetime(num.loc[ms_mask], unit="ms", errors="coerce")

    return out


def _parse_timestamp_series(
    ts_series: pd.Series,
    *,
    timestamp_base_datetime: str | None = None,
    timestamp_unit: str = "D",
) -> pd.Series:
    """
    Parse timestamps robustly:
    - Handles Excel serial date numbers (days since 1899-12-30)
    - Handles unix seconds/milliseconds
    - Falls back to pandas string/datetime parsing
    """
    s = ts_series.copy()

    # If most values are numeric, try numeric-based conversions first.
    s_num = pd.to_numeric(s, errors="coerce")
    numeric_ratio = float(s_num.notna().mean()) if len(s_num) else 0.0

    base_dt = None
    if timestamp_base_datetime is not None and str(timestamp_base_datetime).strip() != "":
        base_dt = pd.to_datetime(timestamp_base_datetime, errors="coerce")
        if pd.isna(base_dt):
            raise ValueError(f"Could not parse timestamp_base_datetime='{timestamp_base_datetime}'")

    if numeric_ratio >= 0.7:
        s_min = float(np.nanmin(s_num.values)) if len(s_num) else float("nan")
        s_max = float(np.nanmax(s_num.values)) if len(s_num) else float("nan")

        # Excel serial date: commonly ~ 40000-60000 for years 2000-2060-ish.
        if np.isfinite(s_min) and np.isfinite(s_max) and (20000 <= s_min <= 80000) and (20000 <= s_max <= 80000):
            return pd.to_datetime(s_num, unit="D", origin="1899-12-30", errors="coerce")

        # Unix seconds: ~ 1e9-1e10; Unix milliseconds: ~ 1e12-1e13.
        if np.isfinite(s_min) and np.isfinite(s_max) and (1e9 <= s_min <= 2e10) and (1e9 <= s_max <= 2e10):
            return pd.to_datetime(s_num, unit="s", errors="coerce")
        if np.isfinite(s_min) and np.isfinite(s_max) and (1e12 <= s_min <= 2e13) and (1e12 <= s_max <= 2e13):
            return pd.to_datetime(s_num, unit="ms", errors="coerce")

        # If it's numeric but doesn't look like Excel serial dates or Unix timestamps,
        # treat it as a relative time index only when a base datetime is provided.
        if base_dt is not None:
            # Interpret numeric values as offsets in `timestamp_unit` from `base_dt`.
            return base_dt + pd.to_timedelta(s_num, unit=timestamp_unit, errors="coerce")
        # Without a base datetime, unknown numeric values are ambiguous. Returning
        # NaT avoids accidental epoch conversions (1970-01-01 style errors).
        return pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    # Even if numeric_ratio is lower, avoid pandas default behavior where numeric
    # values are interpreted as nanoseconds since epoch (leading to 1970-01-01).
    # If we have enough numeric values and we didn't detect a known timestamp unit,
    # treat them as a relative time index.
    if numeric_ratio >= 0.2 and s_num.notna().any():
        if base_dt is not None:
            return base_dt + pd.to_timedelta(s_num, unit=timestamp_unit, errors="coerce")
        return pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    # Fallback: datetime-like strings (prefer US order: M/D/YYYY e.g. 5/3/2022)
    return pd.to_datetime(s, errors="coerce", dayfirst=False)


def _resolve_timestamp_column_index(df_raw: pd.DataFrame, fallback_index: int) -> int:
    """
    Prefer a column named `Timestamp` (case-insensitive) by position.
    Many plant exports put the time column last; older layouts use column 0.
    If no such header exists, use `fallback_index`.
    """
    for i, col in enumerate(df_raw.columns):
        if str(col).strip().lower() == "timestamp":
            return i
    return fallback_index


def load_wide_time_series_xlsx(
    file_path: str,
    *,
    sheet_name=0,
    timestamp_col_index: int = 0,
    timestamp_col_name: str = "Timestamp",
    timestamp_base_datetime: str | None = None,
    timestamp_unit: str = "D",
) -> pd.DataFrame:
    """
    Load a wide-format XLSX time series:
    - If a column is named `Timestamp` (case-insensitive), that column is used.
    - Otherwise column `timestamp_col_index` (default 0) is treated as timestamp.
    - Remaining columns are treated as numeric tag series.
    Returns a DataFrame with a standard `Timestamp` column name.
    """
    df_raw = pd.read_excel(file_path, sheet_name=sheet_name)
    if df_raw.shape[1] < 2:
        raise ValueError("Time-series XLSX must contain at least 2 columns (timestamp + tags).")

    # Important: some XLSX files contain duplicate column headers.
    # Pandas date parsing can crash if `df[timestamp_col_name]` resolves to multiple columns.
    # Resolve timestamp by header name when possible, else by position.
    timestamp_col_index = _resolve_timestamp_column_index(df_raw, timestamp_col_index)

    ts_series = df_raw.iloc[:, timestamp_col_index]

    # Native Excel datetime columns (e.g. Multi_X_Multi_Y_Correct_Data.xlsx: Timestamp last col).
    if pd.api.types.is_datetime64_any_dtype(ts_series):
        timestamp_parsed = pd.to_datetime(ts_series, errors="coerce")
    # Prefer direct parsing for text dates like 5/3/2022 (US month/day/year from Excel export).
    elif ts_series.dtype == object or str(ts_series.dtype).startswith("string"):
        direct_ts = pd.to_datetime(ts_series, errors="coerce", dayfirst=False)
        if direct_ts.notna().mean() >= 0.8:
            timestamp_parsed = direct_ts
        else:
            timestamp_parsed = _parse_timestamp_series(
                ts_series,
                timestamp_base_datetime=timestamp_base_datetime,
                timestamp_unit=timestamp_unit,
            )
            # Fill gaps with US-oriented string parse
            mask = timestamp_parsed.isna() & direct_ts.notna()
            timestamp_parsed = timestamp_parsed.where(~mask, direct_ts)
    else:
        timestamp_parsed = _parse_timestamp_series(
            ts_series,
            timestamp_base_datetime=timestamp_base_datetime,
            timestamp_unit=timestamp_unit,
        )

    # Keep raw first-column values for UI correctness even when parsing fails.
    timestamp_raw = ts_series.copy()

    # Prefer a parsed raw column when it is more complete than the first parse.
    raw_ts_parsed = pd.to_datetime(timestamp_raw, errors="coerce", dayfirst=False)
    if raw_ts_parsed.notna().sum() > timestamp_parsed.notna().sum():
        timestamp_parsed = raw_ts_parsed

    df = pd.DataFrame(
        {
            timestamp_col_name: timestamp_parsed,
            "Timestamp_raw": timestamp_raw,
        }
    )

    # Build numeric tag columns from the remaining positions.
    used_names = {timestamp_col_name}
    for i in range(df_raw.shape[1]):
        if i == timestamp_col_index:
            continue
        raw_col = df_raw.columns[i]
        if raw_col is None or (isinstance(raw_col, float) and pd.isna(raw_col)):
            base_name = f"Tag_{i}"
        else:
            base_name = str(raw_col).strip()
            if base_name.lower() in {"nan", "none", ""}:
                base_name = f"Tag_{i}"

        tag_name = base_name
        if tag_name in used_names:
            tag_name = f"{base_name}_{i}"

        used_names.add(tag_name)
        df[tag_name] = pd.to_numeric(df_raw.iloc[:, i], errors="coerce")

    # Keep rows where the raw timestamp exists; parsing may fail depending on file format.
    df = df.dropna(subset=["Timestamp_raw"]).reset_index(drop=True)

    # Sort for nicer plots when possible.
    if df[timestamp_col_name].notna().any():
        df = df.sort_values(timestamp_col_name).reset_index(drop=True)
    else:
        raw_numeric = pd.to_numeric(df["Timestamp_raw"], errors="coerce")
        if raw_numeric.notna().any():
            df = df.assign(_raw_sort=raw_numeric).sort_values("_raw_sort").drop(columns=["_raw_sort"]).reset_index(drop=True)
    return df

