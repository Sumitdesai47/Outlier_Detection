"""Convert Plotly figures to JSON safe for browser chart libraries (plain arrays, no binary bdata)."""
from __future__ import annotations

import base64
import json
from typing import Any

import numpy as np


def _expand_binary_array(obj: dict) -> list:
    dtype = np.dtype(str(obj.get("dtype") or "f8"))
    raw = base64.b64decode(str(obj.get("bdata") or ""))
    arr = np.frombuffer(raw, dtype=dtype)
    return arr.tolist()


def _plainify_plotly_value(value: Any) -> Any:
    if isinstance(value, dict):
        keys = set(value.keys())
        if keys <= {"dtype", "bdata"} and "bdata" in value:
            try:
                return _expand_binary_array(value)
            except Exception:
                return value
        return {str(k): _plainify_plotly_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plainify_plotly_value(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def plotly_figure_to_client_json(fig) -> dict:
    """Plotly Figure → JSON dict with list x/y arrays (not binary-encoded bdata)."""
    payload = json.loads(fig.to_json())
    return _plainify_plotly_value(payload)
