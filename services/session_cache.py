"""Pickle-backed session storage for Part 2 (anomaly) and Part 3 (outlier) plot/analysis APIs."""
from __future__ import annotations

import os
import pickle
import tempfile
from typing import Any, Dict

_PART3_CACHE: dict[str, dict[str, Any]] = {}
_PART2_CACHE: dict[str, dict[str, Any]] = {}


def part3_cache_file(result_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"anomaly_pt3_{result_id}.pkl")


def part3_store(result_id: str, df_for_script: Any, out_df: Any) -> None:
    ctx = {"df_for_script": df_for_script, "out_df": out_df}
    _PART3_CACHE[result_id] = ctx
    try:
        with open(part3_cache_file(result_id), "wb") as f:
            pickle.dump(ctx, f, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError:
        pass


def part3_load(result_id: str) -> dict[str, Any] | None:
    if result_id in _PART3_CACHE:
        return _PART3_CACHE[result_id]
    path = part3_cache_file(result_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            ctx = pickle.load(f)
        _PART3_CACHE[result_id] = ctx
        return ctx
    except OSError:
        return None


def part2_cache_file(result_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"anomaly_pt2_{result_id}.pkl")


def part2_store(result_id: str, session_blob: Dict[str, Any]) -> None:
    _PART2_CACHE[result_id] = session_blob
    try:
        with open(part2_cache_file(result_id), "wb") as f:
            pickle.dump(session_blob, f, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError:
        pass


def part2_load(result_id: str) -> dict[str, Any] | None:
    if result_id in _PART2_CACHE:
        return _PART2_CACHE[result_id]
    path = part2_cache_file(result_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            ctx = pickle.load(f)
        _PART2_CACHE[result_id] = ctx
        return ctx
    except OSError:
        return None
