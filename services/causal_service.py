from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd


def parse_path_tags(path_text: str) -> List[str]:
    """
    Parse a propagation path like:
      "A -> B -> C" or "A → B → C"
    into a list of tag strings.
    """
    if path_text is None or (isinstance(path_text, float) and pd.isna(path_text)):
        return []
    txt = str(path_text).strip()
    if not txt:
        return []
    parts = re.split(r"->|→", txt)
    return [p.strip() for p in parts if p.strip()]


def _find_propagation_path_column(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lowered = {c: str(c).lower() for c in cols}
    # Prefer columns that include both propagation + path.
    preferred = None
    for c in cols:
        lc = lowered[c]
        if "propagation" in lc and "path" in lc:
            preferred = c
            break
    if preferred:
        return preferred
    # Fallback: any column containing propagation.
    for c in cols:
        if "propagation" in lowered[c]:
            return c
    # As a last resort, return the first non-index column.
    return cols[0]


def extract_child_nodes_from_propagation_paths(
    causal_model_xlsx_path: str,
    *,
    drift_tags: Iterable[str],
    sheet_name: str = "Chain_Matrix_Exhaustive",
    allowed_tags: Optional[Set[str]] = None,
) -> dict:
    """
    From propagation-path strings in the causal model, extract the immediate child node(s)
    after each drift tag.

    Returns:
      - children_set: all discovered children (optionally filtered by allowed_tags)
      - children_by_drift: mapping drift_tag -> list(child_tags)
    """
    drift_tags_set = set([str(t).strip() for t in drift_tags if str(t).strip()])
    if not drift_tags_set:
        return {"children_set": set(), "children_by_drift": {}}

    causal_df = pd.read_excel(causal_model_xlsx_path, sheet_name=sheet_name)
    if causal_df.empty:
        return {"children_set": set(), "children_by_drift": {}}

    path_col = _find_propagation_path_column(causal_df)
    paths = (
        causal_df[path_col]
        .dropna()
        .astype(str)
        .map(str.strip)
        .loc[lambda s: s != ""]
        .unique()
        .tolist()
    )

    children_by_drift: Dict[str, List[str]] = {t: [] for t in drift_tags_set}
    children_set: Set[str] = set()

    for path in paths:
        tags = parse_path_tags(path)
        if not tags:
            continue

        for drift_tag in drift_tags_set:
            if drift_tag not in tags:
                continue
            idx = tags.index(drift_tag)
            if idx < 0 or idx >= len(tags) - 1:
                continue
            child = tags[idx + 1]
            if not child or child == drift_tag:
                continue
            if allowed_tags is not None and child not in allowed_tags:
                continue
            if child not in children_set:
                children_set.add(child)
            if child not in children_by_drift[drift_tag]:
                children_by_drift[drift_tag].append(child)

    return {"children_set": children_set, "children_by_drift": children_by_drift}

