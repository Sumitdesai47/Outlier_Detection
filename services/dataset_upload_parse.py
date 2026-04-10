"""
Parse and validate Excel uploads for plant dataset upload flow.

Assumptions (documented):
- Files must be .xlsx (validated by extension and pandas read).
- Time series: first sheet only, each row becomes one JSON object (column name -> cell value).
- Causal matrix: every sheet is ingested; sheet name is stored with each row.
"""
from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Tuple

import pandas as pd


_ALLOWED_XLSX = re.compile(r"\.xlsx$", re.IGNORECASE)


def validate_excel_filename(name: str | None) -> None:
    if not name or not name.strip():
        raise ValueError("Uploaded file must have a name.")
    if not _ALLOWED_XLSX.search(name.strip()):
        raise ValueError("Only Excel .xlsx files are allowed.")


def _jsonable_cell(v: Any) -> Any:
    """Normalize pandas/Excel cell values for JSON columns."""
    if pd.isna(v):
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, str)):
        return v
    if isinstance(v, float):
        return v
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    try:
        import numpy as np

        if isinstance(v, np.generic):
            return v.item()
    except ImportError:
        pass
    return str(v)


def _row_to_jsonable_dict(row: pd.Series) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in row.items():
        key = str(k).strip() if k is not None else ""
        if not key:
            continue
        out[key] = _jsonable_cell(v)
    return out


def parse_timeseries_xlsx(content: bytes) -> List[Dict[str, Any]]:
    """Return one dict per data row (0-based index matches row order in file)."""
    if not content:
        raise ValueError("Time series file is empty.")
    try:
        df = pd.read_excel(io.BytesIO(content), sheet_name=0, engine="openpyxl")
    except Exception as e:
        raise ValueError(f"Could not read time series Excel: {e}") from e
    if df.empty:
        raise ValueError("Time series sheet has no rows.")
    rows: List[Dict[str, Any]] = []
    for i, (_, series) in enumerate(df.iterrows()):
        rows.append(_row_to_jsonable_dict(series))
    return rows


def parse_causal_matrix_xlsx(content: bytes) -> List[Tuple[str, int, Dict[str, Any]]]:
    """
    Return list of (sheet_name, row_index_within_sheet, row_dict).
    """
    if not content:
        raise ValueError("Causal matrix file is empty.")
    try:
        xl = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
    except Exception as e:
        raise ValueError(f"Could not read causal matrix Excel: {e}") from e
    if not xl.sheet_names:
        raise ValueError("Causal matrix workbook has no sheets.")
    out: List[Tuple[str, int, Dict[str, Any]]] = []
    for sheet in xl.sheet_names:
        df = pd.read_excel(xl, sheet_name=sheet)
        if df.empty:
            continue
        for ridx, (_, series) in enumerate(df.iterrows()):
            out.append((str(sheet), ridx, _row_to_jsonable_dict(series)))
    if not out:
        raise ValueError("Causal matrix workbook has no data rows.")
    return out
