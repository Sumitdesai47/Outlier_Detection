"""
Robust Consensus Outlier Detection (part13; part14/part15 reuse this engine under different UI tab names).

Goal: high precision (very few false positives) on process time series like
``outlier_data_filter_2.xlsx``. A point is flagged only when several
independent statistical tests agree, and isolated single-point blips are
downgraded unless evidence is overwhelming.

Five core signals per (Tag, Timestamp) (S1–S5):
  S1  Global robust z      — |x - clean_median| / (1.4826 * MAD)
  S2  Local rolling z      — same idea against a centered rolling median/MAD
  S3  Tukey outer fence    — x outside [Q1 - k_iqr*IQR, Q3 + k_iqr*IQR]
  S4  First-difference z   — |diff(x)| / (1.4826 * MAD(diff))   (sudden jumps)
  S5  Peer-regression z    — ridge-predict from top correlated peers; residual MAD-z

  S6  Trailing long-window robust z — each point vs trailing median/MAD of the last L
      rows **for that tag only**; catches gradual ramps before the window fully shifts.
  S7  Trend-gap z — robust z of (short trailing median − long trailing median), **per tag**;
      catches sustained separation of recent level from longer history.

When ``early_segment_fraction`` > 0 (part14 preset):

  S8  Early-segment robust z — each point vs median/MAD of the **first** fraction of rows
      in time order for that tag only; catches sustained high/low plateaus vs commissioning
      / early-operation band when later years widened trimmed-global statistics.

The **Dev (Outlier detection)** tab (part15) wires request parsing and preset selection in
``services/dev_outlier_detection_tab.py``; it can pass ``plant_status_filter`` (numeric/string
row filter on one tag) and ``per_tag_controls`` (per-tag threshold scaling vs preset
``k_global_robust_z``, subset of S1–S8 engines, and upward/downward/both detection). See
``run_multi_signal_outlier_detection`` for a DataFrame-first entry point.

Consensus rule (structure unchanged; more rows may be testable when S6–S8 on):
  Actual Outlier  – (S5 peer residual fires AND >= n_act signals) OR (>= n_actual_strict fires;
                    ``Signals_Fired`` can exceed testable count when early-segment "strong" adds a second fire)
  Warning         – exactly n_warning_consensus fire (default 2)
  Normal          – fewer than n_warning_consensus fire

Persistence / isolation:
  An "Actual Outlier" with low neighbor support and fired < isolation_override_consensus
  may be downgraded to "Warning" (see code).

The output dict mirrors the part12 PCA adapter so `templates/results.html`
renders unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.auto_without_causal_outlier_drift import (
    _build_plot_inputs,
    _build_reason,
    _format_ts,
    _safe_float,
    _v5_apply_critical_display_filter,
    clip_plot_inputs_to_wide_timestamps,
    wide_rows_plant_indicator_off,
)
from services.dynamic_tag_group_analysis import build_dynamic_peer_models


# ----------------------------------------------------------------------------
# Configuration — tuned to be conservative (precision over recall).
# ----------------------------------------------------------------------------
CONFIG: Dict[str, Any] = {
    # Signal thresholds (MAD-based z-scores).
    "k_global_robust_z": 4.0,
    "k_local_rolling_z": 4.0,
    "k_iqr_fence": 3.0,           # Tukey k = 3 is "extreme outlier"
    "k_diff_z": 5.0,
    "k_peer_residual_z": 4.0,
    # Local window (centered) for S2 / rolling MAD.
    "local_window": 31,
    "local_min_periods": 11,
    # Peer ridge model (S5).
    "max_peers": 5,
    "min_peer_abs_corr": 0.35,
    "ridge_alpha": 1.0,
    # Dynamic peer selection (services/dynamic_tag_group_analysis.py).
    "use_dynamic_peer_selection": True,
    "dynamic_top_n_correlation": 10,
    "dynamic_top_n_mutual_info": 10,
    "dynamic_top_n_lag": 10,
    "dynamic_max_lag": 5,
    "dynamic_final_top_features": 5,
    "dynamic_cluster_distance_threshold": 0.5,
    # Decision.
    "n_actual_consensus": 3,            # base count for actual outlier (used with the stricter rule below)
    "n_warning_consensus": 2,
    "min_mad": 1e-9,
    # Strict rule: "Actual Outlier" requires either S5 (peer residual) to fire OR >= n_actual_strict signals.
    "n_actual_strict": 4,
    # Bimodal "off-mode" detection (e.g. HPDE_PDI is near-zero ~95% of the time).
    "off_mode_min_fraction": 0.05,      # if >=5% of values are near-zero, treat them as "off" state
    "off_mode_abs_floor": 1e-9,         # absolute floor for off-mode comparison
    "off_mode_rel_floor": 0.10,         # plus relative floor = 10% of robust scale of active subset
    # Isolation downgrade.
    "isolation_neighbor_radius": 2,
    "isolation_override_consensus": 4,
    # Optional: trim each tail (quantile mass) before per-tag median/MAD/IQR.
    # 0 = use full history (default). Small values (e.g. 0.08–0.12) make limits
    # follow dense “typical” operation when a few long high regimes inflate Q3/IQR.
    "baseline_trim_each_tail": 0.0,
    # None = same as baseline_trim_each_tail. Trims |diff| distribution for S4 scale.
    "diff_trim_each_tail": None,
    # Optional trend / regime signals (S6, S7). When long_regime_window <= 0, only S1–S5 run.
    # S6: trailing long-window robust z — value vs median of the last L points (per tag);
    #     catches gradual ramps before the whole window has moved up.
    # S7: robust z of (short trailing median − long trailing median) — sustained separation
    #     of recent level from longer history (per tag).
    "long_regime_window": 0,
    "long_regime_min_periods": 20,
    "k_long_regime_z": 3.5,
    "short_regime_window": 9,
    "short_regime_min_periods": 4,
    "k_trend_gap_z": 3.0,
    # S8: fixed early-window baseline (per tag). Median/MAD from the first fraction
    # of rows in time order — catches sustained levels far from historical startup
    # / commissioning band when later regimes inflated global trimmed stats.
    "early_segment_fraction": 0.0,
    "early_segment_min_points": 40,
    "k_early_segment_z": 3.0,
    # When set < 100: rows with abs(Z_EarlySegment) >= this add a second fire toward
    # consensus (same physical test, stronger evidence — helps gradual ramps vs early history).
    "k_early_segment_strong": 999.0,
}

# Part14 "Multi-signal consensus" tab only — part13 keeps CONFIG above.
# Slightly lower per-signal limits so real excursions reach 3 fires more often;
# n_actual_strict == n_actual_consensus makes "3 of 5" sufficient for Actual
# Outlier (part13 requires 4 unless peer residual also fires). Isolation still
# demotes lone spikes when fired < isolation_override_consensus (4).
MULTI_SIGNAL_PRESET: Dict[str, Any] = {
    "k_global_robust_z": 3.75,
    "k_local_rolling_z": 3.75,
    "k_iqr_fence": 2.75,
    "k_diff_z": 4.5,
    "k_peer_residual_z": 3.75,
    "min_peer_abs_corr": 0.30,
    "n_actual_strict": 3,
    # Per-tag: robust level stats from inner ~70% of values (trim 15% each tail).
    "baseline_trim_each_tail": 0.15,
    # Per-tag trailing windows (rows). Catches ramps like rising pressure over weeks.
    "long_regime_window": 72,
    "long_regime_min_periods": 24,
    "k_long_regime_z": 3.15,
    "short_regime_window": 9,
    "short_regime_min_periods": 4,
    "k_trend_gap_z": 2.85,
    "early_segment_fraction": 0.28,
    "early_segment_min_points": 40,
    "k_early_segment_z": 2.95,
    "k_early_segment_strong": 3.55,
}

# Stable IDs for the eight UI signal engines (Dev / multi-signal tab).
SIGNAL_ENGINE_IDS: Tuple[str, ...] = (
    "S1_GLOBAL",
    "S2_LOCAL",
    "S3_TUKEY",
    "S4_DIFF",
    "S5_PEER",
    "S6_LONG",
    "S7_TREND",
    "S8_EARLY",
)

SIGNAL_ENGINE_LABELS: Dict[str, str] = {
    "S1_GLOBAL": "Global Robust Z",
    "S2_LOCAL": "Local Rolling Z (31 Days)",
    "S3_TUKEY": "Tukey Outer Fence",
    "S4_DIFF": "First-Difference Z",
    "S5_PEER": "Peer Residual Z",
    "S6_LONG": "Long Window Z (trailing regime)",
    "S7_TREND": "Trend-gap Z (short vs long median)",
    "S8_EARLY": "Early Segment Z",
}

_K_SCALE_KEYS: Tuple[str, ...] = (
    "k_global_robust_z",
    "k_local_rolling_z",
    "k_iqr_fence",
    "k_diff_z",
    "k_peer_residual_z",
    "k_long_regime_z",
    "k_trend_gap_z",
    "k_early_segment_z",
    "k_early_segment_strong",
)


# ----------------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------------
def _mad_scale(series: pd.Series, *, min_mad: float) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return float("nan")
    med = float(s.median())
    mad = float((s - med).abs().median())
    if not np.isfinite(mad) or mad <= 0:
        return float(min_mad)
    return float(1.4826 * mad)


def _inner_trim_series(x: pd.Series, trim_each_tail: float, *, min_inner: int) -> pd.Series:
    """Drop outer quantile tails so baseline stats follow dense typical behavior.

    Each tag is trimmed **separately**. If trimming leaves too few points, returns ``x``.
    """
    t = float(trim_each_tail)
    if t <= 0 or x.empty or len(x) < 10:
        return x
    lo_b = float(x.quantile(t))
    hi_b = float(x.quantile(1.0 - t))
    inner = x[(x >= lo_b) & (x <= hi_b)]
    if len(inner) < min_inner:
        return x
    return inner


def _diff_scale_trimmed(v: pd.Series, trim_each_tail: float, *, min_mad: float) -> float:
    d = v.diff().dropna()
    if d.empty:
        return float(min_mad)
    min_inner = max(25, min(80, len(d) // 3))
    inner = _inner_trim_series(d, trim_each_tail, min_inner=min_inner)
    return _mad_scale(inner, min_mad=min_mad)


def _rolling_median_mad(
    series: pd.Series, *, window: int, min_periods: int, min_mad: float
) -> Tuple[pd.Series, pd.Series]:
    s = pd.to_numeric(series, errors="coerce")
    med = s.rolling(window=window, center=True, min_periods=min_periods).median()
    mad = (s - med).abs().rolling(window=window, center=True, min_periods=min_periods).median()
    scale = (1.4826 * mad).clip(lower=min_mad)
    return med, scale


def _trailing_long_regime_z(
    x: pd.Series, *, window: int, min_periods: int, min_mad: float
) -> pd.Series:
    """Trailing-window robust z: compare each point to median/MAD of the preceding window."""
    s = pd.to_numeric(x, errors="coerce")
    trail_med = s.rolling(window=window, min_periods=min_periods).median()
    trail_mad = (s - trail_med).abs().rolling(window=window, min_periods=min_periods).median()
    scale = (1.4826 * trail_mad).clip(lower=min_mad)
    return (s - trail_med) / scale.replace(0, np.nan)


def _short_minus_long_trail_z(
    x: pd.Series,
    *,
    short_w: int,
    short_mp: int,
    long_w: int,
    long_mp: int,
    min_mad: float,
) -> pd.Series:
    """Per-tag trailing short vs long median gap, scaled by tag-wide MAD of the gap."""
    s = pd.to_numeric(x, errors="coerce")
    short_m = s.rolling(window=short_w, min_periods=short_mp).median()
    long_m = s.rolling(window=long_w, min_periods=long_mp).median()
    gap = short_m - long_m
    gap_scale = _mad_scale(gap.dropna(), min_mad=min_mad)
    if not np.isfinite(gap_scale) or gap_scale <= float(min_mad):
        return pd.Series(np.nan, index=s.index)
    return gap / float(gap_scale)


def _build_baseline(df: pd.DataFrame, tag_cols: List[str], cfg: Dict[str, Any]) -> pd.DataFrame:
    """Per-tag robust baseline used by S1 / S3 / S4.

    For tags with a meaningful "off-mode" (>= ``off_mode_min_fraction`` of values
    at/near zero), the baseline statistics are computed on the **active** subset
    only — so the "off" state isn't a perpetual outlier vs the median.

    When ``baseline_trim_each_tail`` > 0, level median/MAD/Q1/Q3/IQR use only the
    inner (1 - 2*trim) quantile band **per tag**, so a separate high regime does not
    pull chart limits and Tukey fences as far as the raw series extrema.
    """
    rows = []
    min_mad = float(cfg["min_mad"])
    abs_floor = float(cfg["off_mode_abs_floor"])
    rel_floor = float(cfg["off_mode_rel_floor"])
    off_min_frac = float(cfg["off_mode_min_fraction"])
    trim = float(cfg.get("baseline_trim_each_tail") or 0.0)
    diff_trim_raw = cfg.get("diff_trim_each_tail")
    diff_trim = float(diff_trim_raw) if diff_trim_raw is not None else trim
    for tag in tag_cols:
        v = pd.to_numeric(df[tag], errors="coerce")
        s = v.dropna()
        if s.empty:
            rows.append(
                dict(
                    Tag=tag, Median=np.nan, MAD_Scale=np.nan, Q1=np.nan, Q3=np.nan,
                    IQR=np.nan, Diff_Scale=np.nan, Count=0,
                    Off_Floor=np.nan, Off_Mode=False,
                )
            )
            continue
        # First pass — global robust stats on **full** series (off-mode detection only).
        med_full = float(s.median())
        mad_full = _mad_scale(s, min_mad=min_mad)
        # Detect off-mode: a meaningful share of values at/near zero relative to MAD.
        off_floor = max(abs_floor, rel_floor * max(mad_full, abs_floor))
        off_frac = float((s.abs() <= off_floor).mean())
        off_mode = off_frac >= off_min_frac
        min_inner = max(25, min(100, int(0.35 * len(s))))
        if off_mode:
            active = s[s.abs() > off_floor]
            if active.empty:
                active = s
            s_level = _inner_trim_series(active, trim, min_inner=min_inner)
            if len(s_level) < min_inner:
                s_level = active
            med = float(s_level.median())
            mad_scale = _mad_scale(s_level, min_mad=min_mad)
            q1, q3 = float(s_level.quantile(0.25)), float(s_level.quantile(0.75))
        else:
            s_level = _inner_trim_series(s, trim, min_inner=min_inner)
            if len(s_level) < min_inner:
                s_level = s
            med = float(s_level.median())
            mad_scale = _mad_scale(s_level, min_mad=min_mad)
            q1, q3 = float(s_level.quantile(0.25)), float(s_level.quantile(0.75))
        iqr = float(q3 - q1)
        if iqr <= 0:
            iqr = mad_scale
        if diff_trim > 0:
            diff_scale = _diff_scale_trimmed(v, diff_trim, min_mad=min_mad)
        else:
            diff_scale = _mad_scale(v.diff(), min_mad=min_mad)
        rows.append(
            dict(
                Tag=tag, Median=med, MAD_Scale=mad_scale, Q1=q1, Q3=q3, IQR=iqr,
                Diff_Scale=diff_scale, Count=int(len(s)),
                Off_Floor=float(off_floor), Off_Mode=bool(off_mode),
            )
        )
    return pd.DataFrame(rows)


def _pick_peers(
    df: pd.DataFrame, tag: str, tag_cols: List[str], cfg: Dict[str, Any]
) -> List[Tuple[str, float]]:
    if len(tag_cols) <= 1:
        return []
    other = [c for c in tag_cols if c != tag]
    if not other:
        return []
    sub = df[[tag] + other].apply(pd.to_numeric, errors="coerce")
    valid = sub.notna().all(axis=1)
    if int(valid.sum()) < 30:
        return []
    corr = sub.loc[valid].corr().get(tag)
    if corr is None or corr.empty:
        return []
    corr = corr.drop(labels=[tag], errors="ignore").dropna()
    corr = corr.reindex(corr.abs().sort_values(ascending=False).index)
    corr = corr[corr.abs() >= float(cfg["min_peer_abs_corr"])]
    if corr.empty:
        return []
    out = []
    for peer, c in corr.head(int(cfg["max_peers"])).items():
        out.append((str(peer), float(c)))
    return out


def _ridge_predict(X: np.ndarray, y: np.ndarray, alpha: float, mask: np.ndarray) -> np.ndarray:
    """Solve (X'X + alpha I) w = X'y on rows where mask is True; predict everywhere."""
    if not mask.any() or X.shape[1] == 0:
        return np.full(X.shape[0], np.nan, dtype=float)
    Xm = X[mask]
    ym = y[mask]
    XtX = Xm.T @ Xm + float(alpha) * np.eye(Xm.shape[1])
    try:
        w = np.linalg.solve(XtX, Xm.T @ ym)
    except np.linalg.LinAlgError:
        return np.full(X.shape[0], np.nan, dtype=float)
    return X @ w


# ----------------------------------------------------------------------------
# Main pipeline.
# ----------------------------------------------------------------------------
def _apply_shutdown_filter(
    df: pd.DataFrame, ts: str, tag_cols: List[str], shutdown_indicator_tags: Optional[Sequence[str]]
) -> pd.DataFrame:
    if not shutdown_indicator_tags:
        return df
    sset = {str(t).strip() for t in shutdown_indicator_tags if t and str(t).strip()}
    sset = {t for t in sset if t in df.columns}
    if not sset:
        return df
    is_shut = wide_rows_plant_indicator_off(df, sorted(sset))
    return df.loc[~is_shut].reset_index(drop=True)


def _plant_drop_mask(df: pd.DataFrame, tag: str, op: str, raw_val: Any) -> pd.Series:
    """Boolean Series: True = drop this row (condition holds for ``tag`` column)."""
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
            drop = pd.Series(np.isclose(left.astype(float), right, equal_nan=False), index=df.index).fillna(False)
        else:
            drop = ~pd.Series(np.isclose(left.astype(float), right, equal_nan=False), index=df.index).fillna(False)
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


def _apply_plant_row_filters(df: pd.DataFrame, ts: str, filters: Optional[List[Dict[str, Any]]]) -> pd.DataFrame:
    """Drop rows where any filter condition is true (OR across filters)."""
    if not filters:
        return df
    combined = pd.Series(False, index=df.index)
    for f in filters:
        if not isinstance(f, dict):
            continue
        tag = str(f.get("status_tag") or f.get("tag") or "").strip()
        op = str(f.get("operator") or "").strip()
        if not tag or not op:
            continue
        combined = combined | _plant_drop_mask(df, tag, op, f.get("value"))
    return df.loc[~combined].reset_index(drop=True)


def _apply_plant_status_condition(
    df: pd.DataFrame, ts: str, plant_status_filter: Optional[Dict[str, Any]]
) -> pd.DataFrame:
    """Drop rows where (status_tag operator value) is True.

    ``plant_status_filter`` keys: enabled (bool), status_tag (str), operator, value.
    """
    if not plant_status_filter or not bool(plant_status_filter.get("enabled")):
        return df
    tag = str(plant_status_filter.get("status_tag") or "").strip()
    op = str(plant_status_filter.get("operator") or "").strip()
    raw_val = plant_status_filter.get("value")
    drop = _plant_drop_mask(df, tag, op, raw_val)
    return df.loc[~drop].reset_index(drop=True)


def _merge_local_cfg_for_tag(
    base_cfg: Dict[str, Any], ptc: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Scale per-signal k-limits when ``threshold`` overrides reference S1 limit."""
    local = dict(base_cfg)
    if not ptc:
        return local
    th = ptc.get("threshold")
    if th is None:
        return local
    try:
        thf = float(th)
    except (TypeError, ValueError):
        return local
    if not np.isfinite(thf) or thf <= 0:
        return local
    ref = float(base_cfg.get("k_global_robust_z") or 1.0)
    if ref <= 0:
        return local
    ratio = thf / ref
    for k in _K_SCALE_KEYS:
        if k in local and isinstance(local[k], (int, float)):
            local[k] = float(local[k]) * ratio
    return local


def _resolve_engine_set(ptc: Optional[Dict[str, Any]]) -> set:
    if not ptc:
        return set(SIGNAL_ENGINE_IDS)
    sel = ptc.get("selected_engines")
    if not sel:
        return set(SIGNAL_ENGINE_IDS)
    s = {str(x).strip() for x in sel if str(x).strip()}
    return s if s else set(SIGNAL_ENGINE_IDS)


def _apply_detection_direction(
    mode: str,
    s1: pd.Series,
    s2: pd.Series,
    s3: pd.Series,
    s4: pd.Series,
    s5: pd.Series,
    s6: pd.Series,
    s7: pd.Series,
    s8: pd.Series,
    s8b: pd.Series,
    *,
    z_global: pd.Series,
    z_local: pd.Series,
    z_diff: pd.Series,
    z_peer: pd.Series,
    z_long: pd.Series,
    z_gap: pd.Series,
    z_early: pd.Series,
    x: pd.Series,
    lo_fence: Any,
    hi_fence: Any,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    m = (mode or "both").strip().lower()
    if m in ("both", "b", ""):
        return s1, s2, s3, s4, s5, s6, s7, s8, s8b
    idx = s1.index
    zg = z_global.reindex(idx)
    zl = z_local.reindex(idx)
    zd = z_diff.reindex(idx)
    zp = z_peer.reindex(idx)
    zlo = z_long.reindex(idx)
    zga = z_gap.reindex(idx)
    ze = z_early.reindex(idx)
    xv = x.reindex(idx)
    if m in ("up", "upward", "u"):
        hi = float(hi_fence) if np.isfinite(hi_fence) else np.inf
        s1 = s1 & (zg > 0)
        s2 = s2 & (zl > 0)
        s3 = s3 & (xv > hi)
        s4 = s4 & (zd > 0)
        s5 = s5 & (zp > 0)
        s6 = s6 & (zlo > 0)
        s7 = s7 & (zga > 0)
        s8 = s8 & (ze > 0)
        s8b = s8b & (ze > 0)
    elif m in ("down", "downward", "d"):
        lo = float(lo_fence) if np.isfinite(lo_fence) else -np.inf
        s1 = s1 & (zg < 0)
        s2 = s2 & (zl < 0)
        s3 = s3 & (xv < lo)
        s4 = s4 & (zd < 0)
        s5 = s5 & (zp < 0)
        s6 = s6 & (zlo < 0)
        s7 = s7 & (zga < 0)
        s8 = s8 & (ze < 0)
        s8b = s8b & (ze < 0)
    return s1, s2, s3, s4, s5, s6, s7, s8, s8b


def _mask_signals_by_engines(
    engines: set,
    s1: pd.Series,
    s2: pd.Series,
    s3: pd.Series,
    s4: pd.Series,
    s5: pd.Series,
    s6: pd.Series,
    s7: pd.Series,
    s8: pd.Series,
    s8b: pd.Series,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    if engines >= set(SIGNAL_ENGINE_IDS):
        return s1, s2, s3, s4, s5, s6, s7, s8, s8b
    z0 = pd.Series(False, index=s1.index)
    return (
        s1 if "S1_GLOBAL" in engines else z0,
        s2 if "S2_LOCAL" in engines else z0,
        s3 if "S3_TUKEY" in engines else z0,
        s4 if "S4_DIFF" in engines else z0,
        s5 if "S5_PEER" in engines else z0,
        s6 if "S6_LONG" in engines else z0,
        s7 if "S7_TREND" in engines else z0,
        s8 if "S8_EARLY" in engines else z0,
        s8b if "S8_EARLY" in engines else z0,
    )


def _map_plot_class(final_class: str) -> str:
    s = str(final_class or "").strip()
    if s == "Actual Outlier":
        return "Strong Anomaly"
    if s == "Warning":
        return "Drift"
    return "Normal"


_LAYMAN_ENGINE_FIRED: Dict[str, str] = {
    "S1_GLOBAL": "Overall level vs normal",
    "S2_LOCAL": "Short-term pattern vs normal",
    "S3_TUKEY": "Outside usual fence limits",
    "S4_DIFF": "Sudden step / jump",
    "S5_PEER": "Does not match related tags",
    "S6_LONG": "Long-window level shift",
    "S7_TREND": "Recent vs longer trend gap",
    "S8_EARLY": "Early-period baseline mismatch",
}


def _layman_failed_engines_list(row: pd.Series) -> str:
    """Comma list of simple names for engines that fired (failed the “normal” test)."""
    out: List[str] = []
    pairs = [
        ("Fire_S1_GLOBAL", "S1_GLOBAL"),
        ("Fire_S2_LOCAL", "S2_LOCAL"),
        ("Fire_S3_TUKEY", "S3_TUKEY"),
        ("Fire_S4_DIFF", "S4_DIFF"),
        ("Fire_S5_PEER", "S5_PEER"),
        ("Fire_S6_LONG", "S6_LONG"),
        ("Fire_S7_TREND", "S7_TREND"),
        ("Fire_S8_EARLY", "S8_EARLY"),
    ]
    for col, eid in pairs:
        try:
            if bool(row.get(col)):
                out.append(_LAYMAN_ENGINE_FIRED.get(eid, SIGNAL_ENGINE_LABELS.get(eid, eid)))
        except Exception:
            pass
    try:
        if bool(row.get("Fire_S8_EARLY_STRONG")):
            out.append("Early period — strong mismatch")
    except Exception:
        pass
    return ", ".join(out) if out else "none"


def _engine_display_names_fired(row: pd.Series) -> str:
    """Official UI engine names that fired (failed the normal test) on this row."""
    names: List[str] = []
    pairs = [
        ("Fire_S1_GLOBAL", "S1_GLOBAL"),
        ("Fire_S2_LOCAL", "S2_LOCAL"),
        ("Fire_S3_TUKEY", "S3_TUKEY"),
        ("Fire_S4_DIFF", "S4_DIFF"),
        ("Fire_S5_PEER", "S5_PEER"),
        ("Fire_S6_LONG", "S6_LONG"),
        ("Fire_S7_TREND", "S7_TREND"),
        ("Fire_S8_EARLY", "S8_EARLY"),
    ]
    for col, eid in pairs:
        try:
            if bool(row.get(col)):
                names.append(str(SIGNAL_ENGINE_LABELS.get(eid, eid)))
        except Exception:
            pass
    try:
        if bool(row.get("Fire_S8_EARLY_STRONG")):
            names.append("Early Segment Z (strong threshold)")
    except Exception:
        pass
    return ", ".join(names) if names else "none"


def _failed_engines_numbered_display_list(row: pd.Series) -> str:
    """Official engine names that fired, numbered 1) 2) … in S1–S8 display order (Strong Anomaly UI)."""
    names: List[str] = []
    pairs = [
        ("Fire_S1_GLOBAL", "S1_GLOBAL"),
        ("Fire_S2_LOCAL", "S2_LOCAL"),
        ("Fire_S3_TUKEY", "S3_TUKEY"),
        ("Fire_S4_DIFF", "S4_DIFF"),
        ("Fire_S5_PEER", "S5_PEER"),
        ("Fire_S6_LONG", "S6_LONG"),
        ("Fire_S7_TREND", "S7_TREND"),
        ("Fire_S8_EARLY", "S8_EARLY"),
    ]
    for col, eid in pairs:
        try:
            if bool(row.get(col)):
                names.append(str(SIGNAL_ENGINE_LABELS.get(eid, eid)))
        except Exception:
            pass
    try:
        if bool(row.get("Fire_S8_EARLY_STRONG")):
            names.append("Early Segment Z (strong threshold)")
    except Exception:
        pass
    if not names:
        return "(none)"
    return "\n".join(f"{i}) {n}" for i, n in enumerate(names, start=1))


def _build_anomaly_explanation_for_details(
    row: pd.Series, *, eng_active: set, cfg: Dict[str, Any]
) -> str:
    """One short block: failed-engine list + one layman sentence (all result tabs)."""
    _ = (eng_active, cfg)  # reserved for future per-tag wording; keep call sites stable.
    fc = str(row.get("Final_Class_Display") or "").strip()
    failed = _layman_failed_engines_list(row)
    direction = str(row.get("Direction") or "").strip().lower()
    if direction.startswith("h") or direction == "high":
        dir_plain = "higher than"
    elif direction.startswith("l") or direction == "low":
        dir_plain = "lower than"
    else:
        dir_plain = "off"

    if fc == "Strong Anomaly":
        numbered = _failed_engines_numbered_display_list(row)
        return (
            "Failed checks (engines):\n"
            f"{numbered}\n"
            f"In simple terms: the reading was clearly {dir_plain} what we treat as normal here, "
            f"and several separate checks agreed at the same moment, so this is flagged as the strongest type of issue."
        )

    if fc in ("Drift", "Drift + Anomaly", "Contextual Anomaly"):
        return (
            f"Failed engines: {failed}. "
            f"In simple terms: something looked off compared with normal, but it did not reach the strongest “all clear failed” bar."
        )

    if fc == "Normal":
        if failed == "none":
            return (
                "Failed engines: none. "
                "In simple terms: nothing here crossed the line to be called an issue for this tag."
            )
        return (
            f"Failed engines: {failed}. "
            "In simple terms: one or two checks moved a little, but not enough together to call this timestamp a problem."
        )

    return f"Failed engines: {failed}. In simple terms: class “{fc}”."


def run_robust_consensus_outlier_ui(
    file_path: str,
    *,
    shutdown_indicator_tags: Optional[Sequence[str]] = None,
    critical_tags: Optional[Sequence[str]] = None,
    config: Optional[Dict[str, Any]] = None,
    extra_summary: Optional[Dict[str, Any]] = None,
    plant_status_filter: Optional[Dict[str, Any]] = None,
    plant_row_filters: Optional[List[Dict[str, Any]]] = None,
    per_tag_controls: Optional[Dict[str, Dict[str, Any]]] = None,
    use_multimodel_s5: bool = False,
    multimodel_s5_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    cfg = CONFIG.copy()
    if config:
        cfg.update(config)

    # ------------------------------------------------------------------
    # 1) Load wide time series (tolerant ingestion, like part8 / part12).
    # ------------------------------------------------------------------
    from services.auto_without_causal_outlier_drift import _load_auto_without_causal_module

    mod = _load_auto_without_causal_module()
    raw_df, _selected_sheet = mod.read_input_file(
        file_path, sheet_name=None, max_rows=None, datetime_format=None
    )
    if raw_df.empty:
        raise ValueError("Selected sheet is empty.")
    ts_detected = mod.detect_timestamp_col(raw_df, override=None, datetime_format=None)
    tag_cols_arg = mod.parse_tag_cols_argument(None)
    long_df, _input_fmt, _dts, _dtag, _dval = mod.make_long_format(
        raw_df,
        timestamp_col=ts_detected,
        tag_col=None,
        value_col=None,
        tag_cols=tag_cols_arg,
        datetime_format=None,
    )
    pivot = mod.build_pivot(long_df)
    if pivot.shape[1] == 0:
        raise ValueError("No tag columns after pivot; check input format.")
    df = pivot.reset_index().copy()
    ts = "Timestamp"
    df[ts] = pd.to_datetime(df[ts], errors="coerce")
    df = df.dropna(subset=[ts]).sort_values(ts).reset_index(drop=True)
    tag_cols = [c for c in df.columns if c != ts]
    for c in tag_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    tag_cols = [c for c in tag_cols if df[c].notna().sum() >= 10]
    if len(tag_cols) < 1:
        raise ValueError("Need at least one numeric tag with >= 10 observations.")
    df = df[[ts] + tag_cols].copy()

    if plant_row_filters:
        df = _apply_plant_row_filters(df, ts, plant_row_filters)
    else:
        df = _apply_plant_status_condition(df, ts, plant_status_filter)
    df = _apply_shutdown_filter(df, ts, tag_cols, shutdown_indicator_tags)
    tag_cols = [c for c in tag_cols if c in df.columns and df[c].notna().sum() >= 10]
    if df.empty or len(tag_cols) < 1:
        raise ValueError("No usable data after plant-status filtering.")
    df = df[[ts] + tag_cols].copy().reset_index(drop=True)

    # ------------------------------------------------------------------
    # 2) Baseline (global robust stats per tag) and peers.
    # ------------------------------------------------------------------
    baseline = _build_baseline(df, tag_cols, cfg)
    base_idx = baseline.set_index("Tag")
    peers_by_tag, x_variables_by_tag = build_dynamic_peer_models(
        df,
        tag_cols,
        cfg,
        fallback_peers_fn=_pick_peers,
    )

    multimodel_s5_by_tag: Dict[str, Dict[str, Any]] = {}
    multimodel_meta_by_tag: Dict[str, Dict[str, Any]] = {}
    if use_multimodel_s5:
        from services.multimodel_outlier.pipeline import (
            build_multimodel_s5_by_tag,
            multimodel_meta_for_ui,
        )

        if multimodel_s5_tags is not None:
            mm_targets = [str(t) for t in multimodel_s5_tags if str(t) in tag_cols]
        else:
            mm_targets = [str(t) for t in tag_cols]
        multimodel_s5_by_tag = build_multimodel_s5_by_tag(df, tag_cols, mm_targets, cfg=None)
        for tag, mm in multimodel_s5_by_tag.items():
            if mm.get("error"):
                multimodel_meta_by_tag[str(tag)] = multimodel_meta_for_ui(mm)
                continue
            if mm.get("x_variables"):
                x_variables_by_tag[str(tag)] = list(mm["x_variables"])
            multimodel_meta_by_tag[str(tag)] = multimodel_meta_for_ui(mm)

    # ------------------------------------------------------------------
    # 3) Build signals per tag (S1–S5 always; S6–S7 when long_regime_window > 0).
    # ------------------------------------------------------------------
    n_rows = len(df)
    records: List[pd.DataFrame] = []
    for tag in tag_cols:
        ptc_row = (per_tag_controls or {}).get(tag) if per_tag_controls else None
        local_cfg = _merge_local_cfg_for_tag(cfg, ptc_row)
        x = pd.to_numeric(df[tag], errors="coerce")
        med = float(base_idx.loc[tag, "Median"])
        scale = float(base_idx.loc[tag, "MAD_Scale"])
        q1 = float(base_idx.loc[tag, "Q1"])
        q3 = float(base_idx.loc[tag, "Q3"])
        iqr = float(base_idx.loc[tag, "IQR"])
        diff_scale = float(base_idx.loc[tag, "Diff_Scale"])
        off_floor = float(base_idx.loc[tag, "Off_Floor"]) if "Off_Floor" in base_idx.columns else 0.0
        off_mode = bool(base_idx.loc[tag, "Off_Mode"]) if "Off_Mode" in base_idx.columns else False

        scale_ok = np.isfinite(scale) and scale > float(local_cfg["min_mad"])
        diff_ok = np.isfinite(diff_scale) and diff_scale > float(local_cfg["min_mad"])
        iqr_ok = np.isfinite(iqr) and iqr > 0

        # "Off-state" rows are reported as Normal regardless of any signal —
        # these are legitimate shutdowns / no-flow conditions, not outliers.
        off_state = x.abs() <= off_floor if off_mode else pd.Series(False, index=x.index)

        # S1 — global robust z (signed).
        if scale_ok and np.isfinite(med):
            z_global = (x - med) / scale
        else:
            z_global = pd.Series(np.nan, index=x.index)

        # S2 — local rolling z.
        loc_med, loc_scale = _rolling_median_mad(
            x, window=int(local_cfg["local_window"]),
            min_periods=int(local_cfg["local_min_periods"]),
            min_mad=float(local_cfg["min_mad"]),
        )
        z_local = (x - loc_med) / loc_scale.replace(0, np.nan)

        # S3 — Tukey outer fence (absolute fence values for display).
        if iqr_ok:
            lo_fence = q1 - float(local_cfg["k_iqr_fence"]) * iqr
            hi_fence = q3 + float(local_cfg["k_iqr_fence"]) * iqr
        else:
            lo_fence = -np.inf
            hi_fence = np.inf
        outside_fence = (x < lo_fence) | (x > hi_fence)

        # S4 — first-difference shock.
        if diff_ok:
            d = x.diff()
            z_diff = d / diff_scale
        else:
            z_diff = pd.Series(np.nan, index=x.index)

        # S5 — peer-regression residual MAD-z (ridge peers or multimodel prediction).
        mm = multimodel_s5_by_tag.get(str(tag)) if multimodel_s5_by_tag else None
        peer_list = peers_by_tag.get(tag) or []
        predicted = pd.Series(np.nan, index=x.index)
        z_peer = pd.Series(np.nan, index=x.index)
        peer_used: List[str] = []
        if mm and not mm.get("error"):
            predicted = pd.Series(mm.get("predicted"), index=x.index)
            z_peer = pd.Series(mm.get("z_peer"), index=x.index)
            peer_used = [
                str(v.get("tag") or v.get("feature_name") or "")
                for v in (mm.get("x_variables") or [])
                if v.get("tag") or v.get("feature_name")
            ][:8]
        elif peer_list:
            peer_names = [p for p, _ in peer_list]
            peer_used = peer_names
            X = df[peer_names].apply(pd.to_numeric, errors="coerce").to_numpy()
            y = x.to_numpy(dtype=float)
            mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
            if mask.sum() >= 30:
                clean_mask = mask & (z_global.abs().fillna(0) < float(local_cfg["k_global_robust_z"]))
                if int(clean_mask.sum()) < max(30, len(peer_names) * 5):
                    clean_mask = mask
                Xb = np.column_stack([np.ones(X.shape[0]), X])
                pred = _ridge_predict(Xb, y, alpha=float(local_cfg["ridge_alpha"]), mask=clean_mask)
                predicted = pd.Series(pred, index=x.index)
                resid = y - pred
                resid_scale = _mad_scale(
                    pd.Series(resid[clean_mask]), min_mad=float(local_cfg["min_mad"])
                )
                if np.isfinite(resid_scale) and resid_scale > float(local_cfg["min_mad"]):
                    z_peer = pd.Series(resid / resid_scale, index=x.index)

        # Fire flags (absolute thresholds). Off-state rows can't fire any signal.
        not_off = ~off_state.fillna(False)
        s1 = (z_global.abs() >= float(local_cfg["k_global_robust_z"])) & not_off
        s2 = (z_local.abs() >= float(local_cfg["k_local_rolling_z"])) & not_off
        s3 = outside_fence.fillna(False) & not_off
        s4 = (z_diff.abs() >= float(local_cfg["k_diff_z"])) & not_off
        s5 = (z_peer.abs() >= float(local_cfg["k_peer_residual_z"])) & not_off

        # S6 / S7 — trailing regime & trend gap (optional; part14 preset enables).
        long_w_cfg = int(local_cfg.get("long_regime_window") or 0)
        if long_w_cfg > 0 and len(x) >= max(25, long_w_cfg // 2):
            Lw = min(long_w_cfg, max(30, len(x) - 1))
            Lmp = min(int(local_cfg["long_regime_min_periods"]), max(10, Lw // 3))
            Sw = min(int(local_cfg["short_regime_window"]), max(5, Lw // 4))
            Sw = min(Sw, max(5, Lw - 1))
            Smp = min(int(local_cfg["short_regime_min_periods"]), max(3, Sw - 2))
            z_long = _trailing_long_regime_z(
                x, window=Lw, min_periods=Lmp, min_mad=float(local_cfg["min_mad"])
            )
            z_gap = _short_minus_long_trail_z(
                x,
                short_w=Sw,
                short_mp=Smp,
                long_w=Lw,
                long_mp=Lmp,
                min_mad=float(local_cfg["min_mad"]),
            )
            s6 = (z_long.abs() >= float(local_cfg["k_long_regime_z"])) & not_off
            s7 = (z_gap.abs() >= float(local_cfg["k_trend_gap_z"])) & not_off
            t6 = z_long.notna()
            t7 = z_gap.notna()
        else:
            z_long = pd.Series(np.nan, index=x.index)
            z_gap = pd.Series(np.nan, index=x.index)
            s6 = pd.Series(False, index=x.index)
            s7 = pd.Series(False, index=x.index)
            t6 = pd.Series(False, index=x.index)
            t7 = pd.Series(False, index=x.index)

        # S8 — early time-window robust z (optional; per tag, time-ordered rows).
        early_frac = float(local_cfg.get("early_segment_fraction") or 0.0)
        early_min = int(local_cfg.get("early_segment_min_points") or 40)
        if early_frac > 0 and len(x) >= early_min + 10:
            n_e = max(early_min, int(len(x) * early_frac))
            n_e = min(n_e, len(x) - 1)
            early_s = x.iloc[:n_e].dropna()
            if len(early_s) >= max(30, early_min // 2):
                med_e = float(early_s.median())
                scale_e = _mad_scale(early_s, min_mad=float(local_cfg["min_mad"]))
                if np.isfinite(scale_e) and scale_e > float(local_cfg["min_mad"]) and np.isfinite(med_e):
                    z_early = (x - med_e) / float(scale_e)
                    s8 = (z_early.abs() >= float(local_cfg["k_early_segment_z"])) & not_off
                    t8 = pd.Series(True, index=x.index)
                else:
                    z_early = pd.Series(np.nan, index=x.index)
                    s8 = pd.Series(False, index=x.index)
                    t8 = pd.Series(False, index=x.index)
            else:
                z_early = pd.Series(np.nan, index=x.index)
                s8 = pd.Series(False, index=x.index)
                t8 = pd.Series(False, index=x.index)
        else:
            z_early = pd.Series(np.nan, index=x.index)
            s8 = pd.Series(False, index=x.index)
            t8 = pd.Series(False, index=x.index)

        k_early_str = float(local_cfg.get("k_early_segment_strong") or 999.0)
        if k_early_str < 100.0 and early_frac > 0:
            s8b = (z_early.abs() >= k_early_str) & not_off
        else:
            s8b = pd.Series(False, index=x.index)

        dir_mode = str((ptc_row or {}).get("direction") or "both")
        s1, s2, s3, s4, s5, s6, s7, s8, s8b = _apply_detection_direction(
            dir_mode,
            s1,
            s2,
            s3,
            s4,
            s5,
            s6,
            s7,
            s8,
            s8b,
            z_global=z_global,
            z_local=z_local,
            z_diff=z_diff,
            z_peer=z_peer,
            z_long=z_long,
            z_gap=z_gap,
            z_early=z_early,
            x=x,
            lo_fence=lo_fence,
            hi_fence=hi_fence,
        )
        eng = _resolve_engine_set(ptc_row)
        s1, s2, s3, s4, s5, s6, s7, s8, s8b = _mask_signals_by_engines(eng, s1, s2, s3, s4, s5, s6, s7, s8, s8b)

        # Testability flags.
        t1 = scale_ok
        t2 = loc_scale.notna() & (loc_scale > float(local_cfg["min_mad"]))
        t3 = iqr_ok
        t4 = diff_ok
        t5 = z_peer.notna()
        e1 = "S1_GLOBAL" in eng
        e2 = "S2_LOCAL" in eng
        e3 = "S3_TUKEY" in eng
        e4 = "S4_DIFF" in eng
        e5 = "S5_PEER" in eng
        e6 = "S6_LONG" in eng
        e7 = "S7_TREND" in eng
        e8 = "S8_EARLY" in eng

        # Count fired and testable per-row.
        fired = (
            s1.fillna(False).astype(int)
            + s2.fillna(False).astype(int)
            + s3.astype(int)
            + s4.fillna(False).astype(int)
            + s5.fillna(False).astype(int)
            + s6.fillna(False).astype(int)
            + s7.fillna(False).astype(int)
            + s8.fillna(False).astype(int)
            + s8b.fillna(False).astype(int)
        )
        testable = (
            int(bool(t1) and e1)
            + (t2.astype(int) * int(e2))
            + int(bool(t3) and e3)
            + int(bool(t4) and e4)
            + (t5.astype(int) * int(e5))
            + (t6.astype(int) * int(e6))
            + (t7.astype(int) * int(e7))
            + (t8.astype(int) * int(e8))
        )

        # Direction / Limit_Crossed.
        if np.isfinite(med):
            direction = np.where(x.to_numpy() >= med, "High", "Low")
        else:
            direction = np.array(["Unknown"] * n_rows, dtype=object)

        within = (x.between(lo_fence, hi_fence)) if iqr_ok else pd.Series(True, index=x.index)
        limit_crossed = np.where(within.fillna(True), "Within_Limits", "Outer_Range")

        # Predicted fallback when peers are not used.
        predicted = predicted.where(predicted.notna(), other=med if np.isfinite(med) else np.nan)

        record = pd.DataFrame(
            {
                "Timestamp": df[ts].values,
                "Tag": tag,
                "Actual_Value": x.values,
                "Predicted_Value": predicted.values,
                "Baseline_Center": med if np.isfinite(med) else np.nan,
                "Baseline_Scale": scale if scale_ok else np.nan,
                "Z_Global": z_global.values,
                "Z_Local": z_local.values,
                "Z_Diff": z_diff.values,
                "Z_Peer": z_peer.values,
                "Z_LongRegime": z_long.values,
                "Z_TrendGap": z_gap.values,
                "Z_EarlySegment": z_early.values,
                "Outside_Fence": s3.values,
                "Fence_Lower": lo_fence,
                "Fence_Upper": hi_fence,
                "Signals_Fired": fired.values,
                "Signals_Testable": testable.values
                if isinstance(testable, pd.Series)
                else int(testable),
                "S5_Peer_Fired": s5.fillna(False).astype(bool).values,
                "Fire_S1_GLOBAL": s1.fillna(False).astype(bool).values,
                "Fire_S2_LOCAL": s2.fillna(False).astype(bool).values,
                "Fire_S3_TUKEY": s3.fillna(False).astype(bool).values,
                "Fire_S4_DIFF": s4.fillna(False).astype(bool).values,
                "Fire_S5_PEER": s5.fillna(False).astype(bool).values,
                "Fire_S6_LONG": s6.fillna(False).astype(bool).values,
                "Fire_S7_TREND": s7.fillna(False).astype(bool).values,
                "Fire_S8_EARLY": s8.fillna(False).astype(bool).values,
                "Fire_S8_EARLY_STRONG": s8b.fillna(False).astype(bool).values,
                "Off_State": off_state.fillna(False).astype(bool).values,
                "Direction": direction,
                "Limit_Crossed": limit_crossed,
                "Top_Peers": ", ".join(peer_used),
            }
        )
        records.append(record)

    all_results = pd.concat(records, ignore_index=True)
    all_results["Timestamp"] = pd.to_datetime(all_results["Timestamp"], errors="coerce")

    # ------------------------------------------------------------------
    # 4) Consensus + persistence/isolation rule.
    # ------------------------------------------------------------------
    n_act = int(cfg["n_actual_consensus"])
    n_warn = int(cfg["n_warning_consensus"])
    n_act_strict = int(cfg["n_actual_strict"])
    overshoot = int(cfg["isolation_override_consensus"])
    radius = int(cfg["isolation_neighbor_radius"])

    classified_parts: List[pd.DataFrame] = []
    for tag, g in all_results.groupby("Tag", sort=False):
        g = g.sort_values("Timestamp").reset_index(drop=True).copy()
        fired = g["Signals_Fired"].astype(int).to_numpy()
        peer_fired = g["S5_Peer_Fired"].astype(bool).to_numpy() if "S5_Peer_Fired" in g.columns else np.zeros(len(g), dtype=bool)
        off_state_arr = g["Off_State"].astype(bool).to_numpy() if "Off_State" in g.columns else np.zeros(len(g), dtype=bool)

        # Strict actual-outlier rule:
        #   (peer-regression S5 fires AND fired >= n_act)  OR  (fired >= n_act_strict)
        actual_strict = (peer_fired & (fired >= n_act)) | (fired >= n_act_strict)
        cls = np.where(
            actual_strict, "Actual Outlier",
            np.where(fired >= n_warn, "Warning", "Normal"),
        )
        # Off-state rows are never outliers.
        cls = np.where(off_state_arr, "Normal", cls)

        # Isolation: a row classified as "Actual Outlier" with fired < overshoot
        # and no neighbor within +-radius also having fired >= n_warn -> Warning.
        if radius > 0 and len(fired) > 0:
            ge_warn = (fired >= n_warn).astype(int)
            window = pd.Series(ge_warn).rolling(
                window=2 * radius + 1, center=True, min_periods=1
            ).sum().to_numpy()
            isolated = (cls == "Actual Outlier") & (window <= 1) & (fired < overshoot)
            cls = np.where(isolated, "Warning", cls)

        g["Final_Class"] = cls
        g["Final_Status"] = cls
        # Ensure Tag column preserved (some pandas versions drop the group key here).
        if "Tag" not in g.columns:
            g["Tag"] = tag
        classified_parts.append(g)
    all_results = (
        pd.concat(classified_parts, ignore_index=True)
        if classified_parts
        else all_results.assign(Final_Class="Normal", Final_Status="Normal")
    )

    # Reasons.
    def _reason(row: pd.Series) -> str:
        parts: List[str] = []
        checks: List[Tuple[str, str, Any]] = [
            ("Z_Global", "global z", cfg["k_global_robust_z"]),
            ("Z_Local", "local z", cfg["k_local_rolling_z"]),
            ("Z_Diff", "diff z", cfg["k_diff_z"]),
            ("Z_Peer", "peer z", cfg["k_peer_residual_z"]),
        ]
        if int(cfg.get("long_regime_window") or 0) > 0:
            checks.extend(
                [
                    ("Z_LongRegime", "long-regime z", cfg["k_long_regime_z"]),
                    ("Z_TrendGap", "trend-gap z", cfg["k_trend_gap_z"]),
                ]
            )
        if float(cfg.get("early_segment_fraction") or 0) > 0:
            checks.append(("Z_EarlySegment", "early-segment z", cfg["k_early_segment_z"]))
        for col, label, k in checks:
            v = row.get(col)
            try:
                if pd.notna(v) and abs(float(v)) >= float(k):
                    parts.append(f"{label}={abs(float(v)):.2f}>={float(k):.1f}")
            except Exception:
                pass
        try:
            kes = float(cfg.get("k_early_segment_strong") or 999.0)
            if kes < 100.0 and float(cfg.get("early_segment_fraction") or 0) > 0:
                v = row.get("Z_EarlySegment")
                if pd.notna(v) and abs(float(v)) >= kes:
                    parts.append(f"early-segment strong z={abs(float(v)):.2f}>={kes:.2f}")
        except Exception:
            pass
        if bool(row.get("Outside_Fence")):
            parts.append("outside IQR fence")
        eng_names = _engine_display_names_fired(row)
        sig = int(row.get("Signals_Fired") or 0)
        if not parts:
            return f"Within consensus limits  (signals={sig})  | Failed engines: {eng_names}"
        return "; ".join(parts) + f"  (signals={sig})  | Failed engines: {eng_names}"

    all_results["Reason"] = all_results.apply(_reason, axis=1)
    all_results["Abs_Z"] = all_results["Z_Global"].abs()
    # Plot category (Normal / Drift / Strong Anomaly) mapped from Final_Status.
    plot_classes = all_results["Final_Status"].map(_map_plot_class)

    # ------------------------------------------------------------------
    # 5) Summary, dashboards.
    # ------------------------------------------------------------------
    total_checks = int(len(all_results))
    actual_rows = int((all_results["Final_Status"] == "Actual Outlier").sum())
    warning_rows = int((all_results["Final_Status"] == "Warning").sum())
    normal_rows = int((all_results["Final_Status"] == "Normal").sum())
    n_peer_gate = int(cfg["n_actual_consensus"])
    n_strict = int(cfg["n_actual_strict"])
    iso_cap = int(cfg["isolation_override_consensus"])
    summary: Dict[str, Any] = {
        "Threshold_Mode": (
            f"Actual Outlier if (S5 peer residual AND >={n_peer_gate} signals fire) OR "
            f">={n_strict} signals fire; Warning at 2; isolation demotes lone Actual rows "
            f"when neighbors lack support and signals<{iso_cap} — robust_consensus_outlier_workflow.py"
        ),
        "Total_Rows": int(len(df)),
        "Total_Tags": int(len(tag_cols)),
        "Total_Tag_Timestamp_Checks": total_checks,
        "Actual_Outlier_Rows": actual_rows,
        "Warning_Rows": warning_rows,
        "Normal_Rows": normal_rows,
        "Actual_Outlier_Rate": round(actual_rows / total_checks, 6) if total_checks else 0.0,
        "S1_Global_Robust_Z_Limit": float(cfg["k_global_robust_z"]),
        "S2_Local_Rolling_Z_Limit": float(cfg["k_local_rolling_z"]),
        "S3_Tukey_IQR_K": float(cfg["k_iqr_fence"]),
        "S4_Diff_Z_Limit": float(cfg["k_diff_z"]),
        "S5_Peer_Residual_Z_Limit": float(cfg["k_peer_residual_z"]),
        "Consensus_Required_For_Actual_Outlier": int(cfg["n_actual_consensus"]),
        "Min_Signals_For_Actual_Without_Peer": int(cfg["n_actual_strict"]),
        "Consensus_Required_For_Warning": int(cfg["n_warning_consensus"]),
        "Isolation_Neighbor_Radius_Rows": int(cfg["isolation_neighbor_radius"]),
        "Isolation_Demote_If_Signals_Below": int(cfg["isolation_override_consensus"]),
        "Baseline_Trim_Each_Tail": float(cfg.get("baseline_trim_each_tail") or 0.0),
        "Diff_Trim_Each_Tail": float(
            cfg["diff_trim_each_tail"]
            if cfg.get("diff_trim_each_tail") is not None
            else float(cfg.get("baseline_trim_each_tail") or 0.0)
        ),
        "Plant_Off_Filter_Tags": ", ".join(sorted(set(shutdown_indicator_tags))) if shutdown_indicator_tags else "",
        "Plant_Status_Row_Filter": str(plant_status_filter) if plant_status_filter and plant_status_filter.get("enabled") else "",
        "Plant_Row_Filters_Count": int(len(plant_row_filters)) if plant_row_filters else 0,
        "Per_Tag_Dev_Controls": len(per_tag_controls) if per_tag_controls else 0,
    }
    if int(cfg.get("long_regime_window") or 0) > 0:
        summary["S6_Long_Regime_Window_Rows"] = int(cfg["long_regime_window"])
        summary["S6_Long_Regime_Z_Limit"] = float(cfg["k_long_regime_z"])
        summary["S7_Short_Window_Rows"] = int(cfg["short_regime_window"])
        summary["S7_Trend_Gap_Z_Limit"] = float(cfg["k_trend_gap_z"])
    if float(cfg.get("early_segment_fraction") or 0) > 0:
        summary["S8_Early_Segment_Fraction"] = float(cfg["early_segment_fraction"])
        summary["S8_Early_Segment_Min_Points"] = int(cfg["early_segment_min_points"])
        summary["S8_Early_Segment_Z_Limit"] = float(cfg["k_early_segment_z"])
        kes = float(cfg.get("k_early_segment_strong") or 999.0)
        if kes < 100.0:
            summary["S8_Early_Segment_Strong_Z"] = kes
    if int(cfg.get("long_regime_window") or 0) > 0 or float(cfg.get("early_segment_fraction") or 0) > 0:
        sig_parts = ["S1-S5"]
        if int(cfg.get("long_regime_window") or 0) > 0:
            sig_parts.append("S6-S7 regime/trend")
        if float(cfg.get("early_segment_fraction") or 0) > 0:
            sig_parts.append("S8 early-segment")
        summary["Threshold_Mode"] = (
            f"Signals ({', '.join(sig_parts)}). Actual if (S5 peer AND >={n_peer_gate} fires) OR "
            f">={n_strict} fires; Warning at {n_warn}; isolation demotes lone Actual when "
            f"neighbors lack support and signals<{iso_cap} — robust_consensus_outlier_workflow.py"
        )
    if extra_summary:
        summary.update(extra_summary)

    # ------------------------------------------------------------------
    # 6) Per-tag bundles for the existing results template.
    # ------------------------------------------------------------------
    display_results = all_results.copy()
    display_results["Final_Class"] = plot_classes  # for plot inputs.

    # Per-tag limits (used by the chart envelope in the UI).
    tag_limits_by_tag: Dict[str, Dict[str, Any]] = {}
    for tag in tag_cols:
        lim_cfg = _merge_local_cfg_for_tag(cfg, (per_tag_controls or {}).get(tag) if per_tag_controls else None)
        med = _safe_float(base_idx.loc[tag, "Median"])
        scale = _safe_float(base_idx.loc[tag, "MAD_Scale"])
        q1 = _safe_float(base_idx.loc[tag, "Q1"])
        q3 = _safe_float(base_idx.loc[tag, "Q3"])
        iqr = _safe_float(base_idx.loc[tag, "IQR"])
        if scale is None or scale < float(cfg["min_mad"]):
            scale = float(cfg["min_mad"])
        drift_z = float(lim_cfg["k_global_robust_z"])
        strong_z = float(lim_cfg["k_global_robust_z"]) * 1.25
        if med is None:
            tag_limits_by_tag[str(tag)] = {
                "baseline_center": None,
                "baseline_scale": scale,
                "drift_lower_limit": None,
                "drift_upper_limit": None,
                "drift_anomaly_lower_limit": None,
                "drift_anomaly_upper_limit": None,
                "strong_anomaly_lower_limit": None,
                "strong_anomaly_upper_limit": None,
            }
            continue
        # Outer fence uses IQR; drift uses robust z; strong uses 1.25 × robust z.
        if iqr and q1 is not None and q3 is not None and iqr > 0:
            outer_lo = q1 - float(lim_cfg["k_iqr_fence"]) * iqr
            outer_hi = q3 + float(lim_cfg["k_iqr_fence"]) * iqr
        else:
            outer_lo = med - strong_z * scale
            outer_hi = med + strong_z * scale
        tag_limits_by_tag[str(tag)] = {
            "baseline_center": med,
            "baseline_scale": scale,
            "drift_lower_limit": med - drift_z * scale,
            "drift_upper_limit": med + drift_z * scale,
            "drift_anomaly_lower_limit": med - drift_z * scale,
            "drift_anomaly_upper_limit": med + drift_z * scale,
            "strong_anomaly_lower_limit": min(med - strong_z * scale, outer_lo),
            "strong_anomaly_upper_limit": max(med + strong_z * scale, outer_hi),
        }

    # Per-tag summaries / details.
    non_normal = all_results[all_results["Final_Status"] != "Normal"].copy()
    tag_summaries: List[Dict[str, Any]] = []
    details_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    monthly_pages_by_tag: Dict[str, List[Dict[str, Any]]] = {}

    if not non_normal.empty:
        for tag, tag_rows in non_normal.groupby("Tag", dropna=True):
            tag_rows = tag_rows.sort_values("Timestamp")
            first = tag_rows.iloc[0]
            status_display = _map_plot_class(str(first.get("Final_Status") or ""))
            tag_summaries.append(
                {
                    "tag": str(tag),
                    "status": status_display,
                    "drift_timestamp": _format_ts(first.get("Timestamp")),
                    "num_drift_points": int(len(tag_rows)),
                }
            )

            all_rows = all_results[all_results["Tag"] == tag].copy()
            all_rows["Final_Class_Display"] = all_rows["Final_Status"].map(_map_plot_class)
            all_rows = all_rows.sort_values("Timestamp", ascending=False)
            eng_active = _resolve_engine_set(
                (per_tag_controls or {}).get(str(tag)) if per_tag_controls else None
            )
            details_by_tag[str(tag)] = [
                {
                    "Timestamp": _format_ts(r.get("Timestamp")),
                    "Actual_Value": _safe_float(r.get("Actual_Value")),
                    "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                    "Final_Class": r.get("Final_Class_Display"),
                    "Direction": str(r.get("Direction") or "Unknown"),
                    "Reason": str(r.get("Reason") or "").strip()
                    or _build_reason(
                        r.get("Final_Class_Display"),
                        r.get("Direction"),
                        r.get("Limit_Crossed"),
                    ),
                    "Event_Reason": (
                        "Process issue/VC"
                        if bool(r.get("Fire_S5_PEER") or r.get("S5_Peer_Fired"))
                        else "Tag issue/Outlier"
                    ),
                    "S5_Peer_Fired": bool(r.get("Fire_S5_PEER") or r.get("S5_Peer_Fired")),
                    "Anomaly_explanation": _build_anomaly_explanation_for_details(
                        r, eng_active=eng_active, cfg=cfg
                    ),
                }
                for _, r in all_rows.iterrows()
            ]

            tmp = all_rows.copy()
            tmp["month_key"] = (
                pd.to_datetime(tmp["Timestamp"], errors="coerce").dt.to_period("M").astype(str)
            )
            pages: List[Dict[str, Any]] = []
            for m in sorted(
                [x for x in tmp["month_key"].dropna().unique().tolist() if x and x != "NaT"],
                reverse=True,
            ):
                month_rows = tmp[tmp["month_key"] == m].copy()
                pages.append(
                    {
                        "month": m,
                        "rows": [
                            {
                                "Timestamp": _format_ts(r.get("Timestamp")),
                                "Actual_Value": _safe_float(r.get("Actual_Value")),
                                "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                                "Final_Class": r.get("Final_Class_Display"),
                                "Direction": str(r.get("Direction") or "Unknown"),
                                "Reason": str(r.get("Reason") or ""),
                                "Event_Reason": (
                                    "Process issue/VC"
                                    if bool(r.get("Fire_S5_PEER") or r.get("S5_Peer_Fired"))
                                    else "Tag issue/Outlier"
                                ),
                                "S5_Peer_Fired": bool(
                                    r.get("Fire_S5_PEER") or r.get("S5_Peer_Fired")
                                ),
                                "Anomaly_explanation": _build_anomaly_explanation_for_details(
                                    r,
                                    eng_active=_resolve_engine_set(
                                        (per_tag_controls or {}).get(str(tag))
                                        if per_tag_controls
                                        else None
                                    ),
                                    cfg=cfg,
                                ),
                            }
                            for _, r in month_rows.iterrows()
                        ],
                    }
                )
            monthly_pages_by_tag[str(tag)] = pages

    top_tags_by_points = sorted(
        tag_summaries, key=lambda r: int(r.get("num_drift_points") or 0), reverse=True
    )

    sudden_jumps_by_tag: Dict[str, int] = {}
    if "Fire_S4_DIFF" in all_results.columns:
        for tag in tag_cols:
            mask = all_results["Tag"].astype(str) == str(tag)
            sudden_jumps_by_tag[str(tag)] = int(
                all_results.loc[mask, "Fire_S4_DIFF"].fillna(False).astype(bool).sum()
            )
    else:
        sudden_jumps_by_tag = {str(tag): 0 for tag in tag_cols}

    # Wide for plotting (uses mapped plot classes).
    wide_plot, out_df = _build_plot_inputs(display_results)
    clip_requested = bool(shutdown_indicator_tags) or bool(
        plant_status_filter and plant_status_filter.get("enabled")
    ) or bool(plant_row_filters)
    if clip_requested:
        wide_plot, out_df = clip_plot_inputs_to_wide_timestamps(
            wide_plot, out_df, df, ts_name=ts
        )

    # Timestamp summary (auto module expects Abnormal label).
    try:
        from services.auto_without_causal_outlier_drift import _load_auto_without_causal_module
        mod_a = _load_auto_without_causal_module()
        ts_df = all_results.copy()
        ts_df["Final_Status"] = np.where(
            ts_df["Final_Status"].astype(str).eq("Actual Outlier"), "Abnormal", "Normal"
        )
        ts_df["Abs_Z"] = pd.to_numeric(ts_df["Z_Global"], errors="coerce").abs()
        timestamp_summary_rows = mod_a.build_timestamp_summary(ts_df).to_dict(orient="records")
    except Exception:
        timestamp_summary_rows = []

    out: Dict[str, Any] = {
        "summary": summary,
        "top_tags_by_points": top_tags_by_points[:10],
        "tag_summaries": tag_summaries,
        "details_by_tag": details_by_tag,
        "monthly_pages_by_tag": monthly_pages_by_tag,
        "df_for_script": wide_plot,
        "out_df": out_df,
        "timestamp_summary_rows": timestamp_summary_rows,
        "tag_limits_by_tag": tag_limits_by_tag,
        "x_variables_by_tag": x_variables_by_tag,
        "peer_selection_mode": (
            "multimodel_s5"
            if use_multimodel_s5
            else ("dynamic_tag_group" if cfg.get("use_dynamic_peer_selection", True) else "pearson_top_k")
        ),
        "sudden_jumps_by_tag": sudden_jumps_by_tag,
        "multimodel_meta_by_tag": multimodel_meta_by_tag,
    }
    _v5_apply_critical_display_filter(out, tag_cols=tag_cols, critical_tags=critical_tags)
    return out


def run_multi_signal_outlier_detection(
    filtered_df: pd.DataFrame,
    tag_config: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    plant_status_filter: Optional[Dict[str, Any]] = None,
    plant_row_filters: Optional[List[Dict[str, Any]]] = None,
    config: Optional[Dict[str, Any]] = None,
    critical_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Streamlit-style adapter: write ``filtered_df`` to a temp XLSX, then run the UI pipeline.

    If ``plant_status_filter`` is also set, filtering runs again inside the pipeline
    (use only one of pre-filtered data *or* ``plant_status_filter`` / ``plant_row_filters``).
    """
    import os
    import tempfile

    tag_config = tag_config or {}
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        tmp_path = f.name
    try:
        filtered_df.to_excel(tmp_path, index=False)
        crit: Optional[List[str]]
        if critical_tags is not None:
            crit = [str(x) for x in critical_tags if str(x).strip()]
        else:
            crit = [str(k) for k in tag_config.keys() if str(k).strip()]
        crit = crit or None
        return run_robust_consensus_outlier_ui(
            tmp_path,
            shutdown_indicator_tags=None,
            critical_tags=crit,
            config=config,
            plant_status_filter=plant_status_filter,
            plant_row_filters=plant_row_filters,
            per_tag_controls=tag_config,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
