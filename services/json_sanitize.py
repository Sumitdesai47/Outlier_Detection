"""Convert nested structures for safe JSON in HTML/Flask responses."""
from __future__ import annotations

import math
from typing import Any


def jsonable(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(x) for x in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, (str, int, bool)):
        return obj
    if hasattr(obj, "item") and callable(getattr(obj, "item", None)):
        try:
            return jsonable(obj.item())
        except Exception:
            pass
    if hasattr(obj, "isoformat") and callable(getattr(obj, "isoformat", None)):
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    if isinstance(obj, float):
        return float(obj)
    return str(obj)
