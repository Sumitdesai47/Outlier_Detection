"""
Streamlit Dev Outlier Detection — **standalone pipeline** (no Flask tab mixing).

Owns:
  - Plant status row dropping before detection
  - Optional MFI / DOL row filters (same semantics as plant status)
  - UI ``tag_config`` → engine ``per_tag_controls`` (threshold, S1–S8 IDs, direction)
  - Wrapper ``run_multi_signal_outlier_detection`` (DataFrame in → dashboard bundle out)
  - Spike-return heuristics on detail rows + ``out_df`` markers
  - Reason text enrichment (engines used vs skipped, peer/cluster hints)

The statistical core remains ``services.robust_consensus_outlier_workflow`` (temp XLSX bridge).
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from services.auto_without_causal_outlier_drift import _load_auto_without_causal_module
from services.robust_consensus_outlier_workflow import (
    MULTI_SIGNAL_PRESET,
    SIGNAL_ENGINE_IDS,
    SIGNAL_ENGINE_LABELS,
    run_multi_signal_outlier_detection as _run_multi_signal_via_tempfile,
)

# Human-readable labels (Streamlit UI) → engine IDs (robust workflow)
ENGINE_LABEL_TO_ID: Dict[str, str] = {v: k for k, v in SIGNAL_ENGINE_LABELS.items()}
ENGINE_OPTIONS_ORDERED: List[str] = [SIGNAL_ENGINE_LABELS[k] for k in SIGNAL_ENGINE_IDS]


def _row_matches_drop_mask(
    df: pd.DataFrame, tag: str, op: str, raw_val: Any
) -> pd.Series:
    """True = row should be **dropped** (same semantics as robust workflow plant mask)."""
    if not tag or tag not in df.columns or op not in {">", ">=", "<", "<=", "==", "!="}:
        return pd.Series(False, index=df.index)
    col = df[tag]
    num_col = pd.to_numeric(col, errors="coerce")
    try:
        cmp_num = float(raw_val)
        use_num = np.isfinite(cmp_num)
    except (TypeError, ValueError):
        cmp_num = float("nan")
        use_num = False

    if use_num:
        left = num_col
        right = cmp_num
        if op == ">":
            drop = left > right
        elif op == ">=":
            drop = left >= right
        elif op == "<":
            drop = left < right
        elif op == "<=":
            drop = left <= right
        elif op == "==":
            drop = pd.Series(
                np.isclose(left.astype(float), right, equal_nan=False), index=df.index
            ).fillna(False)
        else:
            drop = ~pd.Series(
                np.isclose(left.astype(float), right, equal_nan=False), index=df.index
            ).fillna(False)
    else:
        right = raw_val
        if op == ">":
            drop = col > right
        elif op == ">=":
            drop = col >= right
        elif op == "<":
            drop = col < right
        elif op == "<=":
            drop = col <= right
        elif op == "==":
            drop = col.astype(str) == str(right)
        else:
            drop = col.astype(str) != str(right)
    return pd.Series(drop, index=df.index).fillna(False)


def apply_plant_status_filter(
    df: pd.DataFrame, plant_status_filter: Optional[Mapping[str, Any]]
) -> pd.DataFrame:
    """
    Drop rows where (status_tag operator value) is **true** before outlier detection.

    Expected structure::

        {
            "enabled": True,
            "status_tag": "MY_TAG",
            "operator": "<=",
            "value": 1,
        }

    Dropped rows are excluded from training, thresholds, results, and downstream graphs
    because the returned frame is the only input passed to the detector.
    """
    if df is None or df.empty:
        return df
    if not plant_status_filter or not bool(plant_status_filter.get("enabled")):
        return df.reset_index(drop=True)
    tag = str(plant_status_filter.get("status_tag") or "").strip()
    op = str(plant_status_filter.get("operator") or "").strip()
    raw_val = plant_status_filter.get("value")
    drop = _row_matches_drop_mask(df, tag, op, raw_val)
    return df.loc[~drop].reset_index(drop=True)


def apply_additional_filters(
    df: pd.DataFrame, additional_filters: Optional[Mapping[str, Any]]
) -> pd.DataFrame:
    """
    Apply optional MFI / DOL filters. Each enabled block drops rows where (tag op value) holds.
    Multiple enabled blocks are combined with **OR** (any matching row is dropped), mirroring
    multi-condition plant row filters used in operations.
    """
    if df is None or df.empty or not additional_filters:
        return df.reset_index(drop=True)
    combined = pd.Series(False, index=df.index)
    for key in ("MFI", "DOL"):
        block = additional_filters.get(key)
        if not isinstance(block, dict) or not block.get("enabled"):
            continue
        tag = str(block.get("tag") or "").strip()
        op = str(block.get("operator") or "").strip()
        combined = combined | _row_matches_drop_mask(df, tag, op, block.get("value"))
    if not combined.any():
        return df.reset_index(drop=True)
    return df.loc[~combined].reset_index(drop=True)


def validate_timestamp_column(df: pd.DataFrame, ts_col: str = "Timestamp") -> Tuple[bool, str]:
    """Return (ok, message). Ensures a parseable time axis for industrial series."""
    if ts_col not in df.columns:
        return False, f"Missing required column `{ts_col}` after ingest."
    ts = pd.to_datetime(df[ts_col], errors="coerce")
    bad = int(ts.isna().sum())
    if bad == len(df):
        return False, "Timestamp column could not be parsed to datetimes."
    if bad:
        return True, f"Parsed timestamps: {len(df) - bad} ok, {bad} invalid (rows will drop in engine if needed)."
    return True, "Timestamp column validated."


def load_upload_bytes_to_wide_dataframe(
    file_bytes: bytes, *, filename: str = "upload.xlsx"
) -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Load an uploaded workbook into a wide ``Timestamp`` + numeric tag matrix
    (same shape as ``run_robust_consensus_outlier_ui`` expects after its internal pivot).
    """
    mod = _load_auto_without_causal_module()
    suffix = ".xlsx" if filename.lower().endswith(".xlsx") else ""
    if not suffix:
        return pd.DataFrame(), "Only .xlsx uploads are supported for this dashboard."
    path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(file_bytes)
            path = tmp.name
        raw_df, _selected = mod.read_input_file(
            path, sheet_name=None, max_rows=None, datetime_format=None
        )
        if raw_df.empty:
            return pd.DataFrame(), "The selected sheet is empty."
        ts = mod.detect_timestamp_col(raw_df, override=None, datetime_format=None)
        tag_cols_arg = mod.parse_tag_cols_argument(None)
        long_df, _fmt, _a, _b, _c = mod.make_long_format(
            raw_df,
            timestamp_col=ts,
            tag_col=None,
            value_col=None,
            tag_cols=tag_cols_arg,
            datetime_format=None,
        )
        pivot = mod.build_pivot(long_df)
        out = pivot.reset_index().copy()
        out.columns = [str(c) for c in out.columns]
        if ts and ts in out.columns and ts != "Timestamp":
            out = out.rename(columns={ts: "Timestamp"})
        if "Timestamp" not in out.columns:
            return pd.DataFrame(), "Could not resolve a Timestamp column after ingest."
        out["Timestamp"] = pd.to_datetime(out["Timestamp"], errors="coerce")
        out = out.dropna(subset=["Timestamp"]).sort_values("Timestamp").reset_index(drop=True)
        for c in out.columns:
            if c == "Timestamp":
                continue
            out[c] = pd.to_numeric(out[c], errors="coerce")
        return out, None
    except Exception as e:
        return pd.DataFrame(), str(e).strip() or type(e).__name__
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def load_uploaded_streamlit_file(uploaded_file: Any) -> Tuple[pd.DataFrame, Optional[str]]:
    """Adapter for ``st.file_uploader`` file-like objects."""
    name = getattr(uploaded_file, "name", None) or "upload.xlsx"
    raw = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    return load_upload_bytes_to_wide_dataframe(raw, filename=name)


def human_engine_labels_to_ids(selected: Optional[Sequence[str]]) -> List[str]:
    if not selected:
        return list(SIGNAL_ENGINE_IDS)
    out: List[str] = []
    for lab in selected:
        key = ENGINE_LABEL_TO_ID.get(str(lab).strip())
        if key:
            out.append(key)
    return out or list(SIGNAL_ENGINE_IDS)


def ui_tag_config_to_per_tag_controls(
    tag_config: Optional[Mapping[str, Mapping[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """
    Strip Streamlit-only keys (``spike_control``) and map ``selected_engines`` labels → S1–S8.
    """
    if not tag_config:
        return {}
    per: Dict[str, Dict[str, Any]] = {}
    for tag, cfg in tag_config.items():
        t = str(tag).strip()
        if not t or not isinstance(cfg, dict):
            continue
        row: Dict[str, Any] = {}
        if cfg.get("threshold") is not None:
            try:
                row["threshold"] = float(cfg["threshold"])
            except (TypeError, ValueError):
                pass
        labs = cfg.get("selected_engines")
        if isinstance(labs, (list, tuple)) and labs:
            row["selected_engines"] = human_engine_labels_to_ids(list(labs))
        direction = str(cfg.get("direction") or "both").strip().lower()
        if direction in ("up", "up only", "upward", "u"):
            row["direction"] = "up"
        elif direction in ("down", "down only", "downward", "d"):
            row["direction"] = "down"
        else:
            row["direction"] = "both"
        per[t] = row
    return per


def _default_spike_control() -> Dict[str, Any]:
    return {
        "ignore_single_point_spike": True,
        "spike_persistence_points": 2,
        "spike_return_to_normal_window": 3,
    }


def _merge_spike_defaults(sc: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    base = _default_spike_control()
    if isinstance(sc, dict):
        for k, v in sc.items():
            if k not in base:
                continue
            if k in ("spike_persistence_points", "spike_return_to_normal_window"):
                try:
                    base[k] = max(1, int(v))
                except (TypeError, ValueError):
                    pass
            elif k == "ignore_single_point_spike":
                base[k] = bool(v)
    return base


def apply_spike_controls_on_bundle(
    bundle: Dict[str, Any],
    tag_config: Optional[Mapping[str, Mapping[str, Any]]],
) -> None:
    """
    Downgrade very short isolated excursions to **Spike - Returned Normal** in details and out_df.

    Mutates ``bundle`` in place. Uses per-tag ``spike_control`` from the UI ``tag_config``;
    tags without config skip spike relabelling.
    """
    if not tag_config or not bundle:
        return
    details = bundle.get("details_by_tag") or {}

    non_normal_labels = {"Strong Anomaly", "Drift", "Drift + Anomaly", "Contextual Anomaly", "mild_outlier", "sudden_jump", "strong_outlier"}

    for tag, rows in list(details.items()):
        cfg = tag_config.get(str(tag))
        if not isinstance(cfg, dict):
            continue
        sc = _merge_spike_defaults(cfg.get("spike_control"))
        if not rows:
            continue
        df_rows = pd.DataFrame(rows)
        if "Timestamp" not in df_rows.columns or "Final_Class" not in df_rows.columns:
            continue
        df_rows["_ts"] = pd.to_datetime(df_rows["Timestamp"], errors="coerce")
        df_rows = df_rows.sort_values("_ts").reset_index(drop=True)
        fc = df_rows["Final_Class"].astype(str)
        is_exc = fc.isin(non_normal_labels) | fc.str.contains("Anomaly", case=False, na=False)
        idxs = np.where(is_exc.to_numpy())[0]
        if len(idxs) == 0:
            continue
        pers = max(1, int(sc.get("spike_persistence_points") or 2))
        ignore_one = bool(sc.get("ignore_single_point_spike", True))

        downgrade_at: set[int] = set()
        i = 0
        while i < len(idxs):
            j = i
            while j + 1 < len(idxs) and idxs[j + 1] == idxs[j] + 1:
                j += 1
            run_start = int(idxs[i])
            run_end = int(idxs[j])
            run_len = run_end - run_start + 1
            prev_ok = run_start == 0 or not is_exc.iloc[run_start - 1]
            next_ok = run_end >= len(df_rows) - 1 or not is_exc.iloc[run_end + 1]
            short = run_len <= pers
            if ignore_one and run_len == 1 and prev_ok and next_ok:
                downgrade_at.update(range(run_start, run_end + 1))
            elif short and prev_ok and next_ok:
                downgrade_at.update(range(run_start, run_end + 1))
            i = j + 1

        if not downgrade_at:
            continue
        for k in downgrade_at:
            prev = str(df_rows.at[k, "Reason"] or "")
            df_rows.at[k, "Final_Class"] = "Spike - Returned Normal"
            suf = " Short excursion relabelled as spike returned to normal (Streamlit spike control)."
            df_rows.at[k, "Reason"] = (prev + suf).strip()
        df_rows = df_rows.drop(columns=["_ts"], errors="ignore")
        details[str(tag)] = df_rows.to_dict(orient="records")

    bundle["details_by_tag"] = details

    out_df = bundle.get("out_df")
    if isinstance(out_df, pd.DataFrame) and not out_df.empty:
        out_df = out_df.copy()
        for tag, rows in (bundle.get("details_by_tag") or {}).items():
            for r in rows or []:
                if str(r.get("Final_Class")) != "Spike - Returned Normal":
                    continue
                ts = pd.to_datetime(r.get("Timestamp"), errors="coerce")
                if pd.isna(ts):
                    continue
                m = (out_df["Tag"].astype(str) == str(tag)) & (
                    pd.to_datetime(out_df["Timestamp"], errors="coerce") == ts
                )
                out_df.loc[m, "Status"] = "normal"
        bundle["out_df"] = out_df

    summaries = []
    for tag, rows in (bundle.get("details_by_tag") or {}).items():
        n = 0
        spike_only = 0
        for r in rows or []:
            fc = str(r.get("Final_Class") or "")
            if fc == "Spike - Returned Normal":
                spike_only += 1
            elif fc not in ("Normal", ""):
                n += 1
        summaries.append(
            {
                "tag": str(tag),
                "status": "Normal" if n == 0 else "Drift",
                "drift_timestamp": (rows[0].get("Timestamp") if rows else None),
                "num_drift_points": int(n),
                "num_spike_returned_normal": int(spike_only),
            }
        )
    if summaries:
        bundle["tag_summaries"] = sorted(
            summaries, key=lambda s: int(s.get("num_drift_points") or 0), reverse=True
        )
        bundle["top_tags_by_points"] = bundle["tag_summaries"][:10]


def enrich_reasons_with_engine_context(
    bundle: Dict[str, Any],
    tag_config: Optional[Mapping[str, Mapping[str, Any]]],
) -> None:
    """Append skipped engines and plain-language direction / support hints to Reason strings."""
    if not bundle:
        return
    details = bundle.get("details_by_tag") or {}
    all_ids = list(SIGNAL_ENGINE_IDS)
    for tag, rows in details.items():
        cfg = (tag_config or {}).get(str(tag)) or {}
        labs = cfg.get("selected_engines")
        if isinstance(labs, (list, tuple)) and labs:
            active_ids = set(human_engine_labels_to_ids(list(labs)))
        else:
            active_ids = set(SIGNAL_ENGINE_IDS)
        skipped = [SIGNAL_ENGINE_LABELS[i] for i in all_ids if i not in active_ids]
        skipped_txt = ", ".join(skipped) if skipped else "(none — all engines active)"
        direction = str(cfg.get("direction") or "both").strip().lower()
        if direction in ("up", "u", "up only", "upward"):
            dir_txt = "Display/detection direction: upward moves only."
        elif direction in ("down", "d", "down only", "downward"):
            dir_txt = "Display/detection direction: downward moves only."
        else:
            dir_txt = "Display/detection direction: both up and down."

        for r in rows or []:
            if not isinstance(r, dict):
                continue
            cls = str(r.get("Final_Class") or "")
            if cls in ("Normal", "") or cls == "Spike - Returned Normal":
                continue
            peer_hint = ""
            rs = str(r.get("Reason") or "")
            if "peer" in rs.lower():
                peer_hint = " Peer-residual signal contributed."
            else:
                peer_hint = " Limited peer-residual emphasis in the reason text; check Z_Peer in raw export if needed."
            extra = (
                f" Engines evaluated for this tag: active {len(active_ids)}/8. "
                f"Skipped (auto-pass for consensus): {skipped_txt}. {dir_txt}{peer_hint}"
            )
            r["Reason"] = (str(r.get("Reason") or "").strip() + extra).strip()
    bundle["details_by_tag"] = details


def run_multi_signal_outlier_detection(
    filtered_df: pd.DataFrame,
    tag_config: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    plant_status_filter: Optional[Dict[str, Any]] = None,
    additional_filters: Optional[Dict[str, Any]] = None,
    critical_tags: Optional[Sequence[str]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    End-to-end Dev outlier job for Streamlit.

    Parameters
    ----------
    filtered_df :
        Wide dataframe (``Timestamp`` + numeric tag columns) **before** optional plant/MFI/DOL
        filters. If you already applied filters, pass ``plant_status_filter=None`` and
        ``additional_filters=None``.
    tag_config :
        Per-tag UI configuration (threshold, human-readable engine labels, direction, spike_control).
    plant_status_filter / additional_filters :
        Row-removal filters applied **only here**; the robust engine is called with
        ``plant_status_filter=None`` so rows are not dropped twice.

    Returns
    -------
    The standard robust-consensus bundle plus ``streamlit_meta`` (row counts, filter echo).
    """
    if filtered_df is None or filtered_df.empty:
        raise ValueError("Input dataframe is empty.")

    rows_before = int(len(filtered_df))
    df = filtered_df.copy()
    df = apply_plant_status_filter(df, plant_status_filter)
    df = apply_additional_filters(df, additional_filters)
    rows_after = int(len(df))
    dropped = rows_before - rows_after
    if df.empty:
        raise ValueError("No rows left after plant status / MFI / DOL filters.")

    ok, ts_msg = validate_timestamp_column(df, "Timestamp")
    if not ok:
        raise ValueError(ts_msg)

    per_tag = ui_tag_config_to_per_tag_controls(tag_config)
    cfg = dict(config or MULTI_SIGNAL_PRESET)

    crit_list: Optional[List[str]]
    if critical_tags is not None:
        crit_list = [str(x).strip() for x in critical_tags if str(x).strip()]
        crit_list = crit_list or None
    else:
        crit_list = [str(k) for k in (tag_config or {})] or None

    bundle = _run_multi_signal_via_tempfile(
        df,
        per_tag,
        plant_status_filter=None,
        plant_row_filters=None,
        config=cfg,
        critical_tags=crit_list,
    )

    apply_spike_controls_on_bundle(bundle, tag_config)
    enrich_reasons_with_engine_context(bundle, tag_config)

    bundle["streamlit_meta"] = {
        "rows_before_filter": rows_before,
        "rows_after_filter": rows_after,
        "rows_dropped": dropped,
        "plant_status_filter": plant_status_filter,
        "additional_filters": additional_filters,
        "tag_config_echo": tag_config,
        "timestamp_validation_message": ts_msg,
    }
    return bundle


def build_default_tag_config_row(
    *, default_threshold: float, engines: Optional[Sequence[str]] = None
) -> Dict[str, Any]:
    """Seed structure for a new tag row in the Streamlit UI."""
    return {
        "threshold": float(default_threshold),
        "selected_engines": list(engines or ENGINE_OPTIONS_ORDERED),
        "direction": "both",
        "spike_control": _default_spike_control(),
    }
