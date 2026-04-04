"""Load Detail_Pipeline module, build causal graph from chain matrix, prepare smoothed wide data."""
from __future__ import annotations

import importlib.util
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from .causal_service import _find_propagation_path_column, parse_path_tags
from .time_series_utils import format_date_us_mdy, load_wide_time_series_xlsx

_ROOT = Path(__file__).resolve().parents[1]


def load_detail_pipeline_module():
    path = _ROOT / "Detail_Pipeline.py"
    spec = importlib.util.spec_from_file_location("detail_pipeline_internal", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def graph_from_propagation_paths(path_strings: List[str]) -> Dict[str, Any]:
    edges: List[tuple[str, str]] = []
    for path_text in path_strings:
        tags = parse_path_tags(path_text)
        if len(tags) < 2:
            continue
        for i in range(len(tags) - 1):
            src, tgt = tags[i], tags[i + 1]
            if src and tgt and src != tgt:
                edges.append((src, tgt))
    edges = list(set(edges))
    if not edges:
        raise ValueError(
            "No valid edges from propagation paths. Check Chain_Matrix_Exhaustive "
            "and a column containing propagation paths (e.g. 'Propagation Path')."
        )

    parents: dict = defaultdict(set)
    children: dict = defaultdict(set)
    nodes: set = set()
    for src, tgt in edges:
        parents[tgt].add(src)
        children[src].add(tgt)
        nodes.add(src)
        nodes.add(tgt)

    return {
        "edges": edges,
        "parents": parents,
        "children": children,
        "nodes": nodes,
        "sheet_used": "Chain_Matrix_Exhaustive",
        "format_used": "propagation_paths",
    }


def load_causal_graph_from_chain_matrix_excel(causal_xlsx_path: str) -> Dict[str, Any]:
    df = pd.read_excel(causal_xlsx_path, sheet_name="Chain_Matrix_Exhaustive")
    if df.empty:
        raise ValueError("Chain_Matrix_Exhaustive sheet is empty.")
    path_col = _find_propagation_path_column(df)
    paths = (
        df[path_col]
        .dropna()
        .astype(str)
        .map(str.strip)
        .loc[lambda s: s != ""]
        .unique()
        .tolist()
    )
    return graph_from_propagation_paths(paths)


def prepare_smoothed_from_wide_df(
    df: pd.DataFrame,
    *,
    timestamp_col: str,
    lookback_months: int,
    rolling_window: str,
    rolling_min_periods: int,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[str], pd.Timestamp, pd.Timestamp]:
    dp = load_detail_pipeline_module()
    safe_numeric = dp.safe_numeric

    work = df.copy()
    work[timestamp_col] = pd.to_datetime(work[timestamp_col], errors="coerce")
    work = work.dropna(subset=[timestamp_col]).sort_values(timestamp_col).reset_index(drop=True)

    for col in work.columns:
        if col != timestamp_col:
            work[col] = safe_numeric(work[col])

    max_date = work[timestamp_col].max()
    start_date = max_date - pd.DateOffset(months=lookback_months)
    raw_window = work[(work[timestamp_col] >= start_date) & (work[timestamp_col] <= max_date)].copy().reset_index(drop=True)

    numeric_cols = [c for c in raw_window.columns if c != timestamp_col and pd.api.types.is_numeric_dtype(raw_window[c])]

    temp = raw_window[[timestamp_col] + numeric_cols].copy().set_index(timestamp_col)
    for col in numeric_cols:
        temp[col] = temp[col].rolling(rolling_window, min_periods=rolling_min_periods).mean()
    smoothed_df = temp.reset_index()

    return raw_window, smoothed_df, numeric_cols, start_date, max_date


def serialize_row(r: pd.Series) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in r.items():
        if pd.isna(v):
            out[k] = None
        elif hasattr(v, "isoformat"):
            try:
                out[k] = v.isoformat() if hasattr(v, "hour") else str(v)
            except Exception:
                out[k] = str(v)
        else:
            out[k] = v
    return out


def fmt_ts_cell(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "NA"
    return format_date_us_mdy(v)
