from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


def _load_auto_without_causal_module():
    script_path = (
        Path(__file__).resolve().parents[1] / "auto_without_causal_outlier_drift.py"
    )
    spec = importlib.util.spec_from_file_location(
        "auto_without_causal_module", str(script_path)
    )
    if spec is None or spec.loader is None:
        raise ImportError(
            f"Cannot load auto_without_causal_outlier_drift.py from {script_path}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _safe_float(v: Any) -> float | None:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except Exception:
        return None


# Values at or below this (after coercion) count as plant off: 0, negatives, and tiny positives / float noise.
DEFAULT_PLANT_OFF_MAX_VALUE = 1e-6


def wide_rows_plant_indicator_off(
    wide: pd.DataFrame,
    indicator_tag_names: Sequence[str],
    *,
    plant_off_max_value: float = DEFAULT_PLANT_OFF_MAX_VALUE,
) -> pd.Series:
    """
    Per-row mask: True if the plant should be treated as off for that timestamp.

    For each *selected* plant-status column, numeric values **<= plant_off_max_value**
    (default: near zero, including 0 and small positives from float noise) drop that
    timestamp from analysis and charts. Indicator columns stay in the wide matrix.
    Any selected indicator off → drop the whole wide row (OR across indicators).
    """
    cap = float(plant_off_max_value)
    mask = pd.Series(False, index=wide.index)
    for name in indicator_tag_names:
        col = str(name).strip()
        if not col or col not in wide.columns:
            continue
        v = pd.to_numeric(wide[col], errors="coerce")
        off = v.notna() & (v <= cap)
        mask |= off
    return mask


def clip_plot_inputs_to_wide_timestamps(
    wide_plot: pd.DataFrame,
    out_df: pd.DataFrame,
    wide: pd.DataFrame,
    *,
    ts_name: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Restrict line-plot wide and event rows to timestamps still present in ``wide``
    after plant-status row removal so graphs never show dropped timestamps.
    """
    if wide.empty or ts_name not in wide.columns:
        return wide_plot, out_df
    allow = pd.unique(pd.to_datetime(wide[ts_name], errors="coerce").dropna())
    if len(allow) == 0:
        return wide_plot, out_df
    wp = wide_plot
    od = out_df
    if not wp.empty and "Timestamp" in wp.columns:
        tp = pd.to_datetime(wp["Timestamp"], errors="coerce")
        wp = wp.loc[tp.isin(allow)].copy()
        if "Timestamp" in wp.columns:
            wp = wp.sort_values("Timestamp").reset_index(drop=True)
    if not od.empty and "Timestamp" in od.columns:
        tout = pd.to_datetime(od["Timestamp"], errors="coerce")
        od = od.loc[tout.isin(allow)].copy().reset_index(drop=True)
    return wp, od


def filter_rows_to_wide_timestamps(
    df: pd.DataFrame,
    wide: pd.DataFrame,
    *,
    ts_col: str = "Timestamp",
    wide_ts_col: str | None = None,
) -> pd.DataFrame:
    """
    Keep only rows whose Timestamp appears in the wide matrix index/column.

    Used after plant-off / shutdown row removal so long-form results, summaries,
    and plots cannot retain timestamps that were dropped from the working wide data.
    """
    if df.empty or wide.empty or ts_col not in df.columns:
        return df
    wcol = wide_ts_col or ts_col
    if wcol not in wide.columns:
        return df
    allow = pd.unique(pd.to_datetime(wide[wcol], errors="coerce").dropna())
    if len(allow) == 0:
        return df
    ts = pd.to_datetime(df[ts_col], errors="coerce")
    return df.loc[ts.isin(allow)].copy()


def _format_ts(v: Any) -> str:
    ts = pd.to_datetime(v, errors="coerce")
    if pd.isna(ts):
        return str(v) if v is not None else ""
    return ts.strftime("%m/%d/%Y %H:%M")


def preview_workbook_tags_for_part8(file_path: str) -> List[str]:
    """Return sorted unique tag names from a workbook using the same wide/long detection as part8."""
    module = _load_auto_without_causal_module()
    raw_df, _ = module.read_input_file(
        file_path, sheet_name=None, max_rows=5000, datetime_format=None
    )
    timestamp_col = module.detect_timestamp_col(
        raw_df, override=None, datetime_format=None
    )
    tag_cols_arg = module.parse_tag_cols_argument(None)
    long_df, _, _, _, _ = module.make_long_format(
        raw_df,
        timestamp_col=timestamp_col,
        tag_col=None,
        value_col=None,
        tag_cols=tag_cols_arg,
        datetime_format=None,
    )
    return sorted(
        {str(x).strip() for x in long_df["Tag"].dropna().unique() if str(x).strip()}
    )


def _v5_apply_critical_display_filter(
    bundle: Dict[str, Any],
    *,
    tag_cols: List[str],
    critical_tags: Optional[Sequence[str]],
) -> None:
    if not critical_tags:
        return
    crit = {
        str(t).strip()
        for t in critical_tags
        if t is not None and str(t).strip()
    }
    crit &= set(tag_cols)
    if not crit:
        bundle["tag_summaries"] = []
        bundle["top_tags_by_points"] = []
        bundle["details_by_tag"] = {}
        bundle["monthly_pages_by_tag"] = {}
        bundle["tag_limits_by_tag"] = {}
        bundle["x_variables_by_tag"] = {}
        return

    summaries = [
        s
        for s in (bundle.get("tag_summaries") or [])
        if str(s.get("tag")) in crit
    ]
    seen = {str(s.get("tag")) for s in summaries}
    for t in sorted(crit):
        if t not in seen:
            summaries.append(
                {
                    "tag": t,
                    "status": "Normal",
                    "drift_timestamp": None,
                    "num_drift_points": 0,
                }
            )

    def _sort_key(s: Dict[str, Any]) -> Tuple[int, int, str]:
        pts = int(s.get("num_drift_points") or 0)
        is_normal = 1 if pts == 0 else 0
        return (is_normal, -pts, str(s.get("tag") or ""))

    summaries = sorted(summaries, key=_sort_key)
    bundle["tag_summaries"] = summaries
    bundle["top_tags_by_points"] = summaries

    dbt = bundle.get("details_by_tag") or {}
    bundle["details_by_tag"] = {k: v for k, v in dbt.items() if k in crit}
    mby = bundle.get("monthly_pages_by_tag") or {}
    bundle["monthly_pages_by_tag"] = {k: v for k, v in mby.items() if k in crit}
    tlb = bundle.get("tag_limits_by_tag") or {}
    bundle["tag_limits_by_tag"] = {k: v for k, v in tlb.items() if k in crit}
    xvb = bundle.get("x_variables_by_tag") or {}
    bundle["x_variables_by_tag"] = {k: v for k, v in xvb.items() if k in crit}


def _build_reason(final_class: Any, direction: Any, limit_crossed: Any) -> str:
    cls = str(final_class or "").strip()
    direc = str(direction or "").strip()
    lim = str(limit_crossed or "").strip()
    if cls.lower() == "normal":
        return "Within clean baseline limits"
    parts: List[str] = []
    if cls:
        parts.append(cls)
    if direc and direc.upper() not in {"", "NORMAL", "UNKNOWN"}:
        parts.append(f"direction={direc}")
    if lim and lim != "Within_Limits":
        parts.append(f"crossed={lim}")
    return "; ".join(parts) if parts else "Abnormal deviation from baseline"


def _build_plot_inputs(result_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    # Build wide series for line plots.
    wide = (
        result_df.pivot_table(
            index="Timestamp", columns="Tag", values="Actual_Value", aggfunc="mean"
        )
        .sort_index()
        .reset_index()
    )
    wide["Timestamp"] = pd.to_datetime(wide["Timestamp"], errors="coerce")

    # Build event rows that existing plot builder understands.
    cls_map = {
        "Normal": "normal",
        "Drift": "sudden_jump",
        "Contextual Anomaly": "mild_outlier",
        "Drift + Anomaly": "mild_outlier",
        "Strong Anomaly": "strong_outlier",
    }
    out_df = result_df.copy()
    out_df["Timestamp"] = pd.to_datetime(out_df["Timestamp"], errors="coerce")
    out_df["Value"] = pd.to_numeric(out_df["Actual_Value"], errors="coerce")
    out_df["Status"] = out_df["Final_Class"].map(cls_map).fillna("normal")
    out_df = out_df[["Tag", "Timestamp", "Value", "Status"]].copy()
    return wide, out_df


def _apply_per_tag_adaptive_thresholds(
    clean_df: pd.DataFrame,
    limits_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build per-tag adaptive z-thresholds from clean-period distributions.
    This avoids one global threshold being too strict/too loose across tags.
    """
    merged = clean_df.merge(
        limits_df[["Tag", "Baseline_Center", "Baseline_Scale"]],
        on="Tag",
        how="left",
    )
    merged["Abs_Z"] = (
        (pd.to_numeric(merged["Actual_Value"], errors="coerce") - merged["Baseline_Center"])
        / merged["Baseline_Scale"]
    ).abs()
    merged["Abs_Z"] = merged["Abs_Z"].replace([float("inf"), float("-inf")], pd.NA)

    limits_df = limits_df.copy()
    limits_df["Drift_Z"] = 3.0
    limits_df["Drift_Anomaly_Z"] = 3.5
    limits_df["Strong_Anomaly_Z"] = 5.0

    for i, row in limits_df.iterrows():
        tag = row["Tag"]
        vals = pd.to_numeric(
            merged.loc[merged["Tag"] == tag, "Abs_Z"], errors="coerce"
        ).dropna()
        if vals.empty:
            continue

        # Per-tag adaptive limits with safe floors and ordered growth.
        drift_z = max(2.4, float(vals.quantile(0.995)))
        drift_anom_z = max(drift_z + 0.35, float(vals.quantile(0.999)))
        strong_z = max(drift_anom_z + 0.6, float(vals.quantile(0.9995)))

        limits_df.at[i, "Drift_Z"] = round(min(drift_z, 12.0), 4)
        limits_df.at[i, "Drift_Anomaly_Z"] = round(min(drift_anom_z, 14.0), 4)
        limits_df.at[i, "Strong_Anomaly_Z"] = round(min(strong_z, 16.0), 4)

    limits_df["Drift_Lower_Limit"] = (
        limits_df["Baseline_Center"] - limits_df["Drift_Z"] * limits_df["Baseline_Scale"]
    )
    limits_df["Drift_Upper_Limit"] = (
        limits_df["Baseline_Center"] + limits_df["Drift_Z"] * limits_df["Baseline_Scale"]
    )
    limits_df["Drift_Anomaly_Lower_Limit"] = (
        limits_df["Baseline_Center"]
        - limits_df["Drift_Anomaly_Z"] * limits_df["Baseline_Scale"]
    )
    limits_df["Drift_Anomaly_Upper_Limit"] = (
        limits_df["Baseline_Center"]
        + limits_df["Drift_Anomaly_Z"] * limits_df["Baseline_Scale"]
    )
    limits_df["Strong_Anomaly_Lower_Limit"] = (
        limits_df["Baseline_Center"]
        - limits_df["Strong_Anomaly_Z"] * limits_df["Baseline_Scale"]
    )
    limits_df["Strong_Anomaly_Upper_Limit"] = (
        limits_df["Baseline_Center"]
        + limits_df["Strong_Anomaly_Z"] * limits_df["Baseline_Scale"]
    )
    return limits_df


def _build_prediction_models_per_tag(
    pivot: pd.DataFrame,
    limits_df: pd.DataFrame,
    x_variables_by_tag: Dict[str, List[Dict[str, Any]]],
    *,
    min_train_rows: int = 24,
    max_x_vars: int = 5,
) -> Tuple[pd.DataFrame, Dict[str, Dict[str, Any]]]:
    """
    Build per-tag prediction models after limits are detected.
    Model uses tag-specific clean rows and top correlated X variables.
    """
    prediction_wide = pd.DataFrame(index=pivot.index)
    model_meta: Dict[str, Dict[str, Any]] = {}

    limits_map = {
        str(r["Tag"]): {
            "lo": float(r["Drift_Lower_Limit"]),
            "hi": float(r["Drift_Upper_Limit"]),
        }
        for _, r in limits_df.iterrows()
        if r.get("Tag") is not None
    }

    for tag in pivot.columns:
        y_all = pd.to_numeric(pivot[tag], errors="coerce")
        lim = limits_map.get(str(tag))
        if lim is None:
            fallback = y_all.ewm(span=12, adjust=False, min_periods=1).mean().shift(1)
            prediction_wide[str(tag)] = fallback.fillna(y_all.median())
            model_meta[str(tag)] = {
                "model_type": "fallback_ewma_no_limits",
                "train_rows": int(y_all.notna().sum()),
                "x_vars": [],
            }
            continue

        clean_mask = y_all.between(lim["lo"], lim["hi"], inclusive="both")
        x_vars = [str(v.get("tag")) for v in (x_variables_by_tag.get(str(tag)) or []) if v.get("tag")]
        x_vars = [x for x in x_vars if x in pivot.columns and x != tag][:max_x_vars]

        pred_series = None
        train_rows = 0

        if x_vars:
            train_df = pd.DataFrame({"y": y_all, "clean": clean_mask}, index=pivot.index)
            for xv in x_vars:
                train_df[xv] = pd.to_numeric(pivot[xv], errors="coerce")
            train_df = train_df[(train_df["clean"]) & train_df["y"].notna()].dropna(subset=x_vars)
            train_rows = int(len(train_df))

            if train_rows >= min_train_rows:
                X = train_df[x_vars].to_numpy(dtype=float)
                y = train_df["y"].to_numpy(dtype=float)
                X = np.column_stack([np.ones(len(X)), X])
                coef, *_ = np.linalg.lstsq(X, y, rcond=None)

                pred_input = pd.DataFrame(index=pivot.index)
                for xv in x_vars:
                    pred_input[xv] = pd.to_numeric(pivot[xv], errors="coerce")
                valid_pred = pred_input.dropna(subset=x_vars)
                Xp = np.column_stack([np.ones(len(valid_pred)), valid_pred[x_vars].to_numpy(dtype=float)])
                yp = Xp @ coef

                pred_series = pd.Series(index=pivot.index, dtype=float)
                pred_series.loc[valid_pred.index] = yp
                model_meta[str(tag)] = {
                    "model_type": "linear_regression",
                    "train_rows": train_rows,
                    "x_vars": x_vars,
                }

        if pred_series is None:
            fallback = y_all.ewm(span=12, adjust=False, min_periods=1).mean().shift(1)
            pred_series = fallback.fillna(y_all.median())
            model_meta[str(tag)] = {
                "model_type": "fallback_ewma",
                "train_rows": train_rows,
                "x_vars": x_vars,
            }
        else:
            fallback = y_all.ewm(span=12, adjust=False, min_periods=1).mean().shift(1)
            pred_series = pred_series.fillna(fallback).fillna(y_all.median())

        prediction_wide[str(tag)] = pred_series

    return prediction_wide, model_meta


def _run_without_causal_pipeline(
    file_path: str,
    *,
    use_auto_clean_reference: bool,
    threshold_mode: str = "global_default",
    pre_adaptive_limits_fn=None,
) -> Dict[str, Any]:
    """
    Execute the project's root auto_without_causal script logic and return
    UI-ready structures for table + graph rendering.
    """
    module = _load_auto_without_causal_module()

    raw_df, selected_sheet = module.read_input_file(
        file_path, sheet_name=None, max_rows=None, datetime_format=None
    )
    timestamp_col = module.detect_timestamp_col(raw_df, override=None, datetime_format=None)
    tag_cols = module.parse_tag_cols_argument(None)
    long_df, input_format, detected_ts_col, detected_tag_col, detected_value_col = (
        module.make_long_format(
            raw_df,
            timestamp_col=timestamp_col,
            tag_col=None,
            value_col=None,
            tag_cols=tag_cols,
            datetime_format=None,
        )
    )

    pivot = module.build_pivot(long_df)
    if use_auto_clean_reference:
        clean_timestamps, clean_diag = module.auto_detect_clean_timestamps(
            pivot,
            clean_window_fraction=0.15,
            min_clean_points=30,
            clean_trim_quantile=0.85,
            max_clean_fraction=0.35,
        )
    else:
        # "Without clean data" mode: use all timestamps as baseline reference.
        clean_timestamps = pivot.index
        clean_diag = pd.DataFrame(
            {
                "Timestamp": pivot.index,
                "Stability_Score": 0.0,
                "Rolling_Stability_Score": 0.0,
                "Clean_Selected": True,
                "Clean_Detection_Method": "all_rows_without_clean_reference",
            }
        )
    clean_start = pd.Series(clean_timestamps).min()
    clean_end = pd.Series(clean_timestamps).max()

    limits_df = module.calculate_clean_limits(
        long_df,
        clean_timestamps=clean_timestamps,
        baseline_method="std",
        min_clean_points=30,
    )
    base_limits_df = limits_df.copy()
    clean_df = long_df[long_df["Timestamp"].isin(set(clean_timestamps))].copy()
    drift_z, drift_anomaly_z, strong_z = 3.0, 3.5, 5.0
    global_result_df = None
    if threshold_mode == "per_tag_adaptive":
        # Baseline reference for comparison note in summary.
        global_limits_df = module.add_threshold_columns(
            base_limits_df.copy(),
            drift_z=drift_z,
            drift_anomaly_z=drift_anomaly_z,
            strong_z=strong_z,
        )
        global_result_df = module.classify_results(
            long_df,
            global_limits_df,
            clean_timestamps=clean_timestamps,
            clean_period_start=clean_start,
            clean_period_end=clean_end,
        )
        working_limits = limits_df.copy()
        if pre_adaptive_limits_fn is not None:
            working_limits = pre_adaptive_limits_fn(working_limits, clean_df)
        limits_df = _apply_per_tag_adaptive_thresholds(clean_df, working_limits)
    else:
        # Keep current fixed-threshold behavior for existing tabs.
        limits_df = module.add_threshold_columns(
            limits_df,
            drift_z=drift_z,
            drift_anomaly_z=drift_anomaly_z,
            strong_z=strong_z,
        )
    result_df = module.classify_results(
        long_df,
        limits_df,
        clean_timestamps=clean_timestamps,
        clean_period_start=clean_start,
        clean_period_end=clean_end,
    )
    timestamp_summary = module.build_timestamp_summary(result_df)
    summary_df = module.build_summary(
        result=result_df,
        limits_df=limits_df,
        clean_diag=clean_diag,
        selected_sheet=selected_sheet,
        input_format=input_format,
        detected_timestamp_col=detected_ts_col,
        detected_tag_col=detected_tag_col,
        detected_value_col=detected_value_col,
        thresholds=(drift_z, drift_anomaly_z, strong_z),
    )

    abnormal = result_df[result_df["Final_Status"] == "Abnormal"].copy()
    tag_limits_by_tag: Dict[str, Dict[str, Any]] = {}
    for _, lr in limits_df.iterrows():
        t = str(lr.get("Tag") or "").strip()
        if not t:
            continue
        tag_limits_by_tag[t] = {
            "baseline_center": _safe_float(lr.get("Baseline_Center")),
            "baseline_scale": _safe_float(lr.get("Baseline_Scale")),
            "drift_lower_limit": _safe_float(lr.get("Drift_Lower_Limit")),
            "drift_upper_limit": _safe_float(lr.get("Drift_Upper_Limit")),
            "drift_anomaly_lower_limit": _safe_float(lr.get("Drift_Anomaly_Lower_Limit")),
            "drift_anomaly_upper_limit": _safe_float(lr.get("Drift_Anomaly_Upper_Limit")),
            "strong_anomaly_lower_limit": _safe_float(lr.get("Strong_Anomaly_Lower_Limit")),
            "strong_anomaly_upper_limit": _safe_float(lr.get("Strong_Anomaly_Upper_Limit")),
        }

    # Selected tag X-variables: top related tags by absolute correlation in wide series.
    x_variables_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    try:
        corr = pivot.corr(numeric_only=True)
        for tag in pivot.columns:
            if tag not in corr.columns:
                x_variables_by_tag[str(tag)] = []
                continue
            s = corr[tag].drop(labels=[tag], errors="ignore").dropna()
            s = s.reindex(s.abs().sort_values(ascending=False).index)
            x_variables_by_tag[str(tag)] = [
                {"tag": str(other), "corr": _safe_float(val)}
                for other, val in s.head(10).items()
            ]
    except Exception:
        x_variables_by_tag = {str(t): [] for t in pivot.columns}

    prediction_wide: pd.DataFrame | None = None
    model_meta: Dict[str, Dict[str, Any]] = {}
    if threshold_mode == "per_tag_adaptive":
        prediction_wide, model_meta = _build_prediction_models_per_tag(
            pivot, limits_df, x_variables_by_tag
        )
    if abnormal.empty:
        wide, out_df = _build_plot_inputs(result_df)
        return {
            "summary": {str(r["Metric"]): r["Value"] for _, r in summary_df.iterrows()},
            "top_tags_by_points": [],
            "tag_summaries": [],
            "details_by_tag": {},
            "monthly_pages_by_tag": {},
            "df_for_script": wide,
            "out_df": out_df,
            "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
            "tag_limits_by_tag": tag_limits_by_tag,
            "x_variables_by_tag": x_variables_by_tag,
        }

    tag_summaries: List[Dict[str, Any]] = []
    details_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    monthly_pages_by_tag: Dict[str, List[Dict[str, Any]]] = {}

    for tag, tag_rows in abnormal.groupby("Tag", dropna=True):
        tag_rows = tag_rows.sort_values("Timestamp")
        first = tag_rows.iloc[0]
        tag_summaries.append(
            {
                "tag": str(tag),
                "status": str(first.get("Final_Class") or ""),
                "drift_timestamp": _format_ts(first.get("Timestamp")),
                "num_drift_points": int(len(tag_rows)),
            }
        )

        all_rows = result_df[result_df["Tag"] == tag].copy()
        all_rows = all_rows.sort_values("Timestamp", ascending=True)
        if prediction_wide is not None and str(tag) in prediction_wide.columns:
            idx_ts = pd.to_datetime(all_rows["Timestamp"], errors="coerce")
            all_rows["Predicted_Value"] = prediction_wide[str(tag)].reindex(idx_ts).to_numpy()
        else:
            # Fallback for modes that do not build per-tag models.
            actual_num = pd.to_numeric(all_rows["Actual_Value"], errors="coerce")
            pred = actual_num.ewm(span=12, adjust=False, min_periods=1).mean().shift(1)
            pred = pred.fillna(pd.to_numeric(all_rows["Baseline_Center"], errors="coerce"))
            all_rows["Predicted_Value"] = pred
        all_rows = all_rows.sort_values("Timestamp", ascending=False)
        table_rows = all_rows
        details_by_tag[str(tag)] = [
            {
                "Timestamp": _format_ts(r.get("Timestamp")),
                "Actual_Value": _safe_float(r.get("Actual_Value")),
                "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                "Final_Class": r.get("Final_Class"),
                "Direction": r.get("Direction"),
                "Reason": _build_reason(
                    r.get("Final_Class"), r.get("Direction"), r.get("Limit_Crossed")
                ),
            }
            for _, r in table_rows.iterrows()
        ]

        tmp = all_rows.copy()
        tmp["month_key"] = pd.to_datetime(tmp["Timestamp"], errors="coerce").dt.to_period("M").astype(str)
        pages: List[Dict[str, Any]] = []
        for m in sorted([x for x in tmp["month_key"].dropna().unique().tolist() if x and x != "NaT"], reverse=True):
            month_rows = tmp[tmp["month_key"] == m].copy()
            rows = [
                {
                    "Timestamp": _format_ts(r.get("Timestamp")),
                    "Actual_Value": _safe_float(r.get("Actual_Value")),
                    "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                    "Final_Class": r.get("Final_Class"),
                    "Direction": r.get("Direction"),
                    "Reason": _build_reason(
                        r.get("Final_Class"), r.get("Direction"), r.get("Limit_Crossed")
                    ),
                }
                for _, r in month_rows.iterrows()
            ]
            pages.append({"month": m, "rows": rows})
        monthly_pages_by_tag[str(tag)] = pages

    top_tags_by_points = sorted(
        tag_summaries,
        key=lambda r: int(r.get("num_drift_points") or 0),
        reverse=True,
    )[:10]

    summary: Dict[str, Any] = {str(r["Metric"]): r["Value"] for _, r in summary_df.iterrows()}
    if threshold_mode == "per_tag_adaptive" and pre_adaptive_limits_fn is not None:
        summary["Threshold_Mode"] = (
            "Improved hybrid: robust baseline (MAD/IQR/std) + per-tag adaptive z"
        )
    elif threshold_mode == "per_tag_adaptive":
        summary["Threshold_Mode"] = "Per-tag adaptive clean limits"
    else:
        summary["Threshold_Mode"] = "Global fixed clean limits"
    if threshold_mode == "per_tag_adaptive":
        summary["Drift_Z"] = "Per-tag"
        summary["Drift_Anomaly_Z"] = "Per-tag"
        summary["Strong_Anomaly_Z"] = "Per-tag"
        model_lr_tags = sum(
            1
            for m in (model_meta or {}).values()
            if str(m.get("model_type")) == "linear_regression"
        )
        summary["Model_Built_Tags"] = model_lr_tags
        summary["Model_Fallback_Tags"] = max(0, len(model_meta or {}) - model_lr_tags)
        if global_result_df is not None:
            cmp = result_df[["Timestamp", "Tag", "Final_Class"]].merge(
                global_result_df[["Timestamp", "Tag", "Final_Class"]].rename(
                    columns={"Final_Class": "Final_Class_Global"}
                ),
                on=["Timestamp", "Tag"],
                how="inner",
            )
            changed = cmp[cmp["Final_Class"] != cmp["Final_Class_Global"]]
            changed_rows = int(len(changed))
            changed_tags = int(changed["Tag"].nunique()) if changed_rows else 0
            summary["Comparison_vs_Global_Changed_Rows"] = changed_rows
            summary["Comparison_vs_Global_Changed_Tags"] = changed_tags
            summary["Comparison_vs_Global_Changed_Row_Rate"] = (
                round(changed_rows / max(1, len(cmp)), 6) if len(cmp) else 0.0
            )
    wide, out_df = _build_plot_inputs(result_df)

    return {
        "summary": summary,
        "top_tags_by_points": top_tags_by_points,
        "tag_summaries": tag_summaries,
        "details_by_tag": details_by_tag,
        "monthly_pages_by_tag": monthly_pages_by_tag,
        "df_for_script": wide,
        "out_df": out_df,
        "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
        "tag_limits_by_tag": tag_limits_by_tag,
        "x_variables_by_tag": x_variables_by_tag,
    }


def _run_clean_anchored_v3_identification_pipeline(file_path: str) -> Dict[str, Any]:
    """
    Auto Identification (part6): without_causal_clean_anchored_improved_v3.py
    (clean-anchored similar-history limits + persistence).
    """
    import without_causal_clean_anchored_improved_v3 as v3

    module = _load_auto_without_causal_module()
    raw_df, selected_sheet = module.read_input_file(
        file_path, sheet_name=None, max_rows=None, datetime_format=None
    )
    timestamp_col = module.detect_timestamp_col(raw_df, override=None, datetime_format=None)
    tag_cols_arg = module.parse_tag_cols_argument(None)
    long_df, input_format, detected_ts_col, detected_tag_col, detected_value_col = (
        module.make_long_format(
            raw_df,
            timestamp_col=timestamp_col,
            tag_col=None,
            value_col=None,
            tag_cols=tag_cols_arg,
            datetime_format=None,
        )
    )
    pivot = module.build_pivot(long_df)
    if pivot.shape[1] == 0:
        raise ValueError("No tag columns after pivot; check input format.")

    wide = pivot.reset_index()
    ts_name = "Timestamp"
    wide[ts_name] = pd.to_datetime(wide[ts_name], errors="coerce")
    wide = wide.dropna(subset=[ts_name]).sort_values(ts_name).reset_index(drop=True)
    tag_cols = [c for c in wide.columns if c != ts_name]
    for c in tag_cols:
        wide[c] = pd.to_numeric(wide[c], errors="coerce")

    cfg = v3.CONFIG.copy()
    clean_df, clean_info, _clean_score = v3.detect_clean_window(
        wide, tag_cols, ts_name, cfg
    )
    limits_df, _masks = v3.build_tag_reference_limits(wide, clean_df, tag_cols, cfg)
    all_results = v3.generate_without_causal_results(
        wide, tag_cols, ts_name, limits_df, cfg
    )

    result_df = all_results.copy()
    result_df["Timestamp"] = pd.to_datetime(result_df["Timestamp"], errors="coerce")
    result_df["Limit_Crossed"] = np.where(
        result_df["Limit_Status"].astype(str) == "Within Limit",
        "Within_Limits",
        result_df["Limit_Status"].astype(str),
    )
    result_df["Abs_Z"] = pd.to_numeric(
        result_df["Abs_Distance_Z_From_Limit"], errors="coerce"
    )
    result_df["Baseline_Center"] = pd.to_numeric(
        result_df["Reference_Median"], errors="coerce"
    )

    timestamp_summary = module.build_timestamp_summary(result_df)

    summary_df = v3.create_summary(
        wide, tag_cols, clean_info, limits_df, all_results, None, cfg
    )
    summary: Dict[str, Any] = {
        str(r["Metric"]): r["Value"] for _, r in summary_df.iterrows()
    }
    summary["Threshold_Mode"] = (
        "Clean-anchored similar-history limits (without_causal_clean_anchored_improved_v3)"
    )
    summary["Selected_Excel_Sheet"] = selected_sheet if selected_sheet is not None else ""
    summary["Input_Format_Detected"] = input_format
    summary["Detected_Timestamp_Column"] = detected_ts_col
    summary["Detected_Tag_Column"] = detected_tag_col
    summary["Detected_Value_Column"] = detected_value_col
    summary["Drift_Z"] = cfg["drift_z"]
    summary["Drift_Anomaly_Z"] = cfg["drift_anomaly_z"]
    summary["Strong_Anomaly_Z"] = cfg["strong_anomaly_z"]

    clean_timestamps, _clean_diag = module.auto_detect_clean_timestamps(
        pivot,
        clean_window_fraction=0.15,
        min_clean_points=30,
        clean_trim_quantile=0.85,
        max_clean_fraction=0.35,
    )
    clean_start = pd.Series(clean_timestamps).min()
    clean_end = pd.Series(clean_timestamps).max()
    global_limits_df = module.calculate_clean_limits(
        long_df,
        clean_timestamps=clean_timestamps,
        baseline_method="std",
        min_clean_points=30,
    )
    global_limits_df = module.add_threshold_columns(
        global_limits_df,
        drift_z=3.0,
        drift_anomaly_z=3.5,
        strong_z=5.0,
    )
    global_result_df = module.classify_results(
        long_df,
        global_limits_df,
        clean_timestamps=clean_timestamps,
        clean_period_start=clean_start,
        clean_period_end=clean_end,
    )
    cmp = result_df[["Timestamp", "Tag", "Final_Class"]].merge(
        global_result_df[["Timestamp", "Tag", "Final_Class"]].rename(
            columns={"Final_Class": "Final_Class_Global"}
        ),
        on=["Timestamp", "Tag"],
        how="inner",
    )
    changed = cmp[cmp["Final_Class"] != cmp["Final_Class_Global"]]
    changed_rows = int(len(changed))
    summary["Comparison_vs_Global_Changed_Rows"] = changed_rows
    summary["Comparison_vs_Global_Changed_Tags"] = (
        int(changed["Tag"].nunique()) if changed_rows else 0
    )
    summary["Comparison_vs_Global_Changed_Row_Rate"] = (
        round(changed_rows / max(1, len(cmp)), 6) if len(cmp) else 0.0
    )

    tag_limits_by_tag: Dict[str, Dict[str, Any]] = {}
    for _, r in limits_df.iterrows():
        tag = str(r.get("Tag") or "").strip()
        if not tag:
            continue
        lo_f = _safe_float(r.get("Lower_Limit"))
        hi_f = _safe_float(r.get("Upper_Limit"))
        tag_limits_by_tag[tag] = {
            "baseline_center": _safe_float(r.get("Reference_Median")),
            "baseline_scale": _safe_float(r.get("Severity_Scale")),
            "drift_lower_limit": lo_f,
            "drift_upper_limit": hi_f,
            "drift_anomaly_lower_limit": lo_f,
            "drift_anomaly_upper_limit": hi_f,
            "strong_anomaly_lower_limit": lo_f,
            "strong_anomaly_upper_limit": hi_f,
        }

    x_variables_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    try:
        corr = pivot.corr(numeric_only=True)
        for tag in pivot.columns:
            if tag not in corr.columns:
                x_variables_by_tag[str(tag)] = []
                continue
            s = corr[tag].drop(labels=[tag], errors="ignore").dropna()
            s = s.reindex(s.abs().sort_values(ascending=False).index)
            x_variables_by_tag[str(tag)] = [
                {"tag": str(other), "corr": _safe_float(val)}
                for other, val in s.head(10).items()
            ]
    except Exception:
        x_variables_by_tag = {str(t): [] for t in pivot.columns}

    limits_for_model = pd.DataFrame(
        [
            {
                "Tag": str(tag),
                "Baseline_Center": float(
                    (tag_limits_by_tag[str(tag)].get("baseline_center") or 0.0)
                ),
                "Baseline_Scale": float(
                    tag_limits_by_tag[str(tag)].get("baseline_scale") or 1e-9
                ),
                "Drift_Lower_Limit": float(
                    (tag_limits_by_tag[str(tag)].get("drift_lower_limit") or 0.0)
                ),
                "Drift_Upper_Limit": float(
                    (tag_limits_by_tag[str(tag)].get("drift_upper_limit") or 0.0)
                ),
            }
            for tag in tag_cols
            if str(tag) in tag_limits_by_tag
        ]
    )
    prediction_wide, model_meta = _build_prediction_models_per_tag(
        pivot, limits_for_model, x_variables_by_tag
    )
    model_lr_tags = sum(
        1
        for m in model_meta.values()
        if str(m.get("model_type")) == "linear_regression"
    )
    summary["Model_Built_Tags"] = model_lr_tags
    summary["Model_Fallback_Tags"] = max(0, len(model_meta) - model_lr_tags)

    abnormal = result_df[result_df["Final_Status"] == "Abnormal"].copy()
    if abnormal.empty:
        wide_plot, out_df = _build_plot_inputs(result_df)
        return {
            "summary": summary,
            "top_tags_by_points": [],
            "tag_summaries": [],
            "details_by_tag": {},
            "monthly_pages_by_tag": {},
            "df_for_script": wide_plot,
            "out_df": out_df,
            "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
            "tag_limits_by_tag": tag_limits_by_tag,
            "x_variables_by_tag": x_variables_by_tag,
        }

    tag_summaries: List[Dict[str, Any]] = []
    details_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    monthly_pages_by_tag: Dict[str, List[Dict[str, Any]]] = {}

    for tag, tag_rows in abnormal.groupby("Tag", dropna=True):
        tag_rows = tag_rows.sort_values("Timestamp")
        first = tag_rows.iloc[0]
        tag_summaries.append(
            {
                "tag": str(tag),
                "status": str(first.get("Final_Class") or ""),
                "drift_timestamp": _format_ts(first.get("Timestamp")),
                "num_drift_points": int(len(tag_rows)),
            }
        )

        all_rows = result_df[result_df["Tag"] == tag].copy()
        all_rows = all_rows.sort_values("Timestamp", ascending=True)
        if str(tag) in prediction_wide.columns:
            idx_ts = pd.to_datetime(all_rows["Timestamp"], errors="coerce")
            all_rows["Predicted_Value"] = prediction_wide[str(tag)].reindex(idx_ts).to_numpy()
        else:
            actual_num = pd.to_numeric(all_rows["Actual_Value"], errors="coerce")
            pred = actual_num.ewm(span=12, adjust=False, min_periods=1).mean().shift(1)
            pred = pred.fillna(pd.to_numeric(all_rows["Baseline_Center"], errors="coerce"))
            all_rows["Predicted_Value"] = pred
        all_rows = all_rows.sort_values("Timestamp", ascending=False)
        details_by_tag[str(tag)] = [
            {
                "Timestamp": _format_ts(r.get("Timestamp")),
                "Actual_Value": _safe_float(r.get("Actual_Value")),
                "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                "Final_Class": r.get("Final_Class"),
                "Direction": r.get("Direction"),
                "Reason": _build_reason(
                    r.get("Final_Class"), r.get("Direction"), r.get("Limit_Crossed")
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
                            "Final_Class": r.get("Final_Class"),
                            "Direction": r.get("Direction"),
                            "Reason": _build_reason(
                                r.get("Final_Class"),
                                r.get("Direction"),
                                r.get("Limit_Crossed"),
                            ),
                        }
                        for _, r in month_rows.iterrows()
                    ],
                }
            )
        monthly_pages_by_tag[str(tag)] = pages

    top_tags_by_points = sorted(
        tag_summaries,
        key=lambda r: int(r.get("num_drift_points") or 0),
        reverse=True,
    )[:10]

    wide_plot, out_df = _build_plot_inputs(result_df)

    return {
        "summary": summary,
        "top_tags_by_points": top_tags_by_points,
        "tag_summaries": tag_summaries,
        "details_by_tag": details_by_tag,
        "monthly_pages_by_tag": monthly_pages_by_tag,
        "df_for_script": wide_plot,
        "out_df": out_df,
        "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
        "tag_limits_by_tag": tag_limits_by_tag,
        "x_variables_by_tag": x_variables_by_tag,
    }


def _run_auto_clean_outlier_pipeline(file_path: str) -> Dict[str, Any]:
    """
    Auto (No Clean Data) / part5: without_causal_auto_clean_outlier.py
    Auto stable window + limits from that window only + V3-style persistence scoring.
    Global comparison uses full-timeline baseline (all timestamps as reference).
    """
    import without_causal_auto_clean_outlier as ac
    import without_causal_clean_anchored_improved_v3 as v3

    module = _load_auto_without_causal_module()
    raw_df, selected_sheet = module.read_input_file(
        file_path, sheet_name=None, max_rows=None, datetime_format=None
    )
    timestamp_col = module.detect_timestamp_col(raw_df, override=None, datetime_format=None)
    tag_cols_arg = module.parse_tag_cols_argument(None)
    long_df, input_format, detected_ts_col, detected_tag_col, detected_value_col = (
        module.make_long_format(
            raw_df,
            timestamp_col=timestamp_col,
            tag_col=None,
            value_col=None,
            tag_cols=tag_cols_arg,
            datetime_format=None,
        )
    )
    pivot = module.build_pivot(long_df)
    if pivot.shape[1] == 0:
        raise ValueError("No tag columns after pivot; check input format.")

    wide = pivot.reset_index()
    ts_name = "Timestamp"
    wide[ts_name] = pd.to_datetime(wide[ts_name], errors="coerce")
    wide = wide.dropna(subset=[ts_name]).sort_values(ts_name).reset_index(drop=True)
    tag_cols = [c for c in wide.columns if c != ts_name]
    for c in tag_cols:
        wide[c] = pd.to_numeric(wide[c], errors="coerce")

    cfg = ac.CONFIG.copy()
    clean_df, clean_info, _clean_score = v3.detect_clean_window(
        wide, tag_cols, ts_name, cfg
    )
    limits_df = ac.build_tag_limits_clean_window_only(wide, clean_df, tag_cols, cfg)
    all_results = v3.generate_without_causal_results(
        wide, tag_cols, ts_name, limits_df, cfg
    )

    result_df = all_results.copy()
    result_df["Timestamp"] = pd.to_datetime(result_df["Timestamp"], errors="coerce")
    result_df["Limit_Crossed"] = np.where(
        result_df["Limit_Status"].astype(str) == "Within Limit",
        "Within_Limits",
        result_df["Limit_Status"].astype(str),
    )
    result_df["Abs_Z"] = pd.to_numeric(
        result_df["Abs_Distance_Z_From_Limit"], errors="coerce"
    )
    result_df["Baseline_Center"] = pd.to_numeric(
        result_df["Reference_Median"], errors="coerce"
    )

    timestamp_summary = module.build_timestamp_summary(result_df)

    summary_df = ac.create_summary_auto_clean(
        wide, tag_cols, clean_info, limits_df, all_results, None, cfg
    )
    summary: Dict[str, Any] = {
        str(r["Metric"]): r["Value"] for _, r in summary_df.iterrows()
    }
    summary["Threshold_Mode"] = (
        "Auto clean window only (without_causal_auto_clean_outlier)"
    )
    summary["Selected_Excel_Sheet"] = selected_sheet if selected_sheet is not None else ""
    summary["Input_Format_Detected"] = input_format
    summary["Detected_Timestamp_Column"] = detected_ts_col
    summary["Detected_Tag_Column"] = detected_tag_col
    summary["Detected_Value_Column"] = detected_value_col
    summary["Drift_Z"] = cfg["drift_z"]
    summary["Drift_Anomaly_Z"] = cfg["drift_anomaly_z"]
    summary["Strong_Anomaly_Z"] = cfg["strong_anomaly_z"]

    clean_start = pd.Series(pivot.index).min()
    clean_end = pd.Series(pivot.index).max()
    global_limits_df = module.calculate_clean_limits(
        long_df,
        clean_timestamps=pivot.index,
        baseline_method="std",
        min_clean_points=30,
    )
    global_limits_df = module.add_threshold_columns(
        global_limits_df,
        drift_z=3.0,
        drift_anomaly_z=3.5,
        strong_z=5.0,
    )
    global_result_df = module.classify_results(
        long_df,
        global_limits_df,
        clean_timestamps=pivot.index,
        clean_period_start=clean_start,
        clean_period_end=clean_end,
    )
    cmp = result_df[["Timestamp", "Tag", "Final_Class"]].merge(
        global_result_df[["Timestamp", "Tag", "Final_Class"]].rename(
            columns={"Final_Class": "Final_Class_Global"}
        ),
        on=["Timestamp", "Tag"],
        how="inner",
    )
    changed = cmp[cmp["Final_Class"] != cmp["Final_Class_Global"]]
    changed_rows = int(len(changed))
    summary["Comparison_vs_Global_Changed_Rows"] = changed_rows
    summary["Comparison_vs_Global_Changed_Tags"] = (
        int(changed["Tag"].nunique()) if changed_rows else 0
    )
    summary["Comparison_vs_Global_Changed_Row_Rate"] = (
        round(changed_rows / max(1, len(cmp)), 6) if len(cmp) else 0.0
    )
    summary["Comparison_vs_Global_Note"] = (
        "Global baseline = all timestamps (std limits), vs auto clean-window limits."
    )

    tag_limits_by_tag: Dict[str, Dict[str, Any]] = {}
    for _, r in limits_df.iterrows():
        tag = str(r.get("Tag") or "").strip()
        if not tag:
            continue
        lo_f = _safe_float(r.get("Lower_Limit"))
        hi_f = _safe_float(r.get("Upper_Limit"))
        tag_limits_by_tag[tag] = {
            "baseline_center": _safe_float(r.get("Reference_Median")),
            "baseline_scale": _safe_float(r.get("Severity_Scale")),
            "drift_lower_limit": lo_f,
            "drift_upper_limit": hi_f,
            "drift_anomaly_lower_limit": lo_f,
            "drift_anomaly_upper_limit": hi_f,
            "strong_anomaly_lower_limit": lo_f,
            "strong_anomaly_upper_limit": hi_f,
        }

    x_variables_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    try:
        corr = pivot.corr(numeric_only=True)
        for tag in pivot.columns:
            if tag not in corr.columns:
                x_variables_by_tag[str(tag)] = []
                continue
            s = corr[tag].drop(labels=[tag], errors="ignore").dropna()
            s = s.reindex(s.abs().sort_values(ascending=False).index)
            x_variables_by_tag[str(tag)] = [
                {"tag": str(other), "corr": _safe_float(val)}
                for other, val in s.head(10).items()
            ]
    except Exception:
        x_variables_by_tag = {str(t): [] for t in pivot.columns}

    limits_for_model = pd.DataFrame(
        [
            {
                "Tag": str(tag),
                "Baseline_Center": float(
                    (tag_limits_by_tag[str(tag)].get("baseline_center") or 0.0)
                ),
                "Baseline_Scale": float(
                    tag_limits_by_tag[str(tag)].get("baseline_scale") or 1e-9
                ),
                "Drift_Lower_Limit": float(
                    (tag_limits_by_tag[str(tag)].get("drift_lower_limit") or 0.0)
                ),
                "Drift_Upper_Limit": float(
                    (tag_limits_by_tag[str(tag)].get("drift_upper_limit") or 0.0)
                ),
            }
            for tag in tag_cols
            if str(tag) in tag_limits_by_tag
        ]
    )
    prediction_wide, model_meta = _build_prediction_models_per_tag(
        pivot, limits_for_model, x_variables_by_tag
    )
    model_lr_tags = sum(
        1
        for m in model_meta.values()
        if str(m.get("model_type")) == "linear_regression"
    )
    summary["Model_Built_Tags"] = model_lr_tags
    summary["Model_Fallback_Tags"] = max(0, len(model_meta) - model_lr_tags)

    abnormal = result_df[result_df["Final_Status"] == "Abnormal"].copy()
    if abnormal.empty:
        wide_plot, out_df = _build_plot_inputs(result_df)
        return {
            "summary": summary,
            "top_tags_by_points": [],
            "tag_summaries": [],
            "details_by_tag": {},
            "monthly_pages_by_tag": {},
            "df_for_script": wide_plot,
            "out_df": out_df,
            "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
            "tag_limits_by_tag": tag_limits_by_tag,
            "x_variables_by_tag": x_variables_by_tag,
        }

    tag_summaries: List[Dict[str, Any]] = []
    details_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    monthly_pages_by_tag: Dict[str, List[Dict[str, Any]]] = {}

    for tag, tag_rows in abnormal.groupby("Tag", dropna=True):
        tag_rows = tag_rows.sort_values("Timestamp")
        first = tag_rows.iloc[0]
        tag_summaries.append(
            {
                "tag": str(tag),
                "status": str(first.get("Final_Class") or ""),
                "drift_timestamp": _format_ts(first.get("Timestamp")),
                "num_drift_points": int(len(tag_rows)),
            }
        )

        all_rows = result_df[result_df["Tag"] == tag].copy()
        all_rows = all_rows.sort_values("Timestamp", ascending=True)
        if str(tag) in prediction_wide.columns:
            idx_ts = pd.to_datetime(all_rows["Timestamp"], errors="coerce")
            all_rows["Predicted_Value"] = prediction_wide[str(tag)].reindex(idx_ts).to_numpy()
        else:
            actual_num = pd.to_numeric(all_rows["Actual_Value"], errors="coerce")
            pred = actual_num.ewm(span=12, adjust=False, min_periods=1).mean().shift(1)
            pred = pred.fillna(pd.to_numeric(all_rows["Baseline_Center"], errors="coerce"))
            all_rows["Predicted_Value"] = pred
        all_rows = all_rows.sort_values("Timestamp", ascending=False)
        details_by_tag[str(tag)] = [
            {
                "Timestamp": _format_ts(r.get("Timestamp")),
                "Actual_Value": _safe_float(r.get("Actual_Value")),
                "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                "Final_Class": r.get("Final_Class"),
                "Direction": r.get("Direction"),
                "Reason": _build_reason(
                    r.get("Final_Class"), r.get("Direction"), r.get("Limit_Crossed")
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
                            "Final_Class": r.get("Final_Class"),
                            "Direction": r.get("Direction"),
                            "Reason": _build_reason(
                                r.get("Final_Class"),
                                r.get("Direction"),
                                r.get("Limit_Crossed"),
                            ),
                        }
                        for _, r in month_rows.iterrows()
                    ],
                }
            )
        monthly_pages_by_tag[str(tag)] = pages

    top_tags_by_points = sorted(
        tag_summaries,
        key=lambda r: int(r.get("num_drift_points") or 0),
        reverse=True,
    )[:10]

    wide_plot, out_df = _build_plot_inputs(result_df)

    return {
        "summary": summary,
        "top_tags_by_points": top_tags_by_points,
        "tag_summaries": tag_summaries,
        "details_by_tag": details_by_tag,
        "monthly_pages_by_tag": monthly_pages_by_tag,
        "df_for_script": wide_plot,
        "out_df": out_df,
        "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
        "tag_limits_by_tag": tag_limits_by_tag,
        "x_variables_by_tag": x_variables_by_tag,
    }


def _run_deviation_spike_v4_testing_pipeline(file_path: str) -> Dict[str, Any]:
    """
    Testing tab (part7): without_causal_clean_deviation_spike_change_v4.py
    Clean window from deviation/spike/change/volatility score; clean-like reference;
    classify with persistence and spike/change flags. Same UI as part5.
    Global comparison = full-timeline std baseline (same as Auto No Clean Data).
    """
    import without_causal_clean_deviation_spike_change_v4 as v4

    module = _load_auto_without_causal_module()
    raw_df, selected_sheet = module.read_input_file(
        file_path, sheet_name=None, max_rows=None, datetime_format=None
    )
    timestamp_col = module.detect_timestamp_col(raw_df, override=None, datetime_format=None)
    tag_cols_arg = module.parse_tag_cols_argument(None)
    long_df, input_format, detected_ts_col, detected_tag_col, detected_value_col = (
        module.make_long_format(
            raw_df,
            timestamp_col=timestamp_col,
            tag_col=None,
            value_col=None,
            tag_cols=tag_cols_arg,
            datetime_format=None,
        )
    )
    pivot = module.build_pivot(long_df)
    if pivot.shape[1] == 0:
        raise ValueError("No tag columns after pivot; check input format.")

    wide = pivot.reset_index()
    ts_name = "Timestamp"
    wide[ts_name] = pd.to_datetime(wide[ts_name], errors="coerce")
    wide = wide.dropna(subset=[ts_name]).sort_values(ts_name).reset_index(drop=True)
    tag_cols = [c for c in wide.columns if c != ts_name]
    for c in tag_cols:
        wide[c] = pd.to_numeric(wide[c], errors="coerce")

    cfg = v4.CONFIG.copy()
    tag_features = v4.compute_tag_deviation_features(wide, tag_cols, cfg)
    clean_df, clean_info, clean_score_df = v4.detect_clean_window(
        wide, tag_cols, ts_name, tag_features, cfg
    )
    limits_df, reference_masks = v4.build_reference_limits(
        wide, clean_df, tag_cols, tag_features, clean_score_df, cfg
    )
    all_results = v4.generate_without_causal_results(
        wide, tag_cols, ts_name, limits_df, reference_masks, tag_features, cfg
    )

    result_df = all_results.copy()
    result_df["Timestamp"] = pd.to_datetime(result_df["Timestamp"], errors="coerce")
    result_df["Limit_Crossed"] = np.where(
        result_df["Limit_Status"].astype(str) == "Within Limit",
        "Within_Limits",
        result_df["Limit_Status"].astype(str),
    )
    result_df["Abs_Z"] = pd.to_numeric(result_df["Abs_Value_Z"], errors="coerce")
    result_df["Baseline_Center"] = pd.to_numeric(
        result_df["Reference_Median"], errors="coerce"
    )

    timestamp_summary = module.build_timestamp_summary(result_df)
    row_status = v4.create_row_status(all_results, ts_name)
    summary_df = v4.create_summary(
        wide,
        tag_cols,
        clean_info,
        limits_df,
        all_results,
        row_status,
        None,
        cfg,
    )
    summary: Dict[str, Any] = {
        str(r["Metric"]): r["Value"] for _, r in summary_df.iterrows()
    }
    summary["Threshold_Mode"] = (
        "Deviation / spike / change V4 (without_causal_clean_deviation_spike_change_v4)"
    )
    summary["Selected_Excel_Sheet"] = selected_sheet if selected_sheet is not None else ""
    summary["Input_Format_Detected"] = input_format
    summary["Detected_Timestamp_Column"] = detected_ts_col
    summary["Detected_Tag_Column"] = detected_tag_col
    summary["Detected_Value_Column"] = detected_value_col
    summary["Drift_Z"] = cfg["drift_z"]
    summary["Drift_Anomaly_Z"] = cfg["drift_anomaly_z"]
    summary["Strong_Anomaly_Z"] = cfg["strong_anomaly_z"]

    clean_start = pd.Series(pivot.index).min()
    clean_end = pd.Series(pivot.index).max()
    global_limits_df = module.calculate_clean_limits(
        long_df,
        clean_timestamps=pivot.index,
        baseline_method="std",
        min_clean_points=30,
    )
    global_limits_df = module.add_threshold_columns(
        global_limits_df,
        drift_z=3.0,
        drift_anomaly_z=3.5,
        strong_z=5.0,
    )
    global_result_df = module.classify_results(
        long_df,
        global_limits_df,
        clean_timestamps=pivot.index,
        clean_period_start=clean_start,
        clean_period_end=clean_end,
    )
    cmp = result_df[["Timestamp", "Tag", "Final_Class"]].merge(
        global_result_df[["Timestamp", "Tag", "Final_Class"]].rename(
            columns={"Final_Class": "Final_Class_Global"}
        ),
        on=["Timestamp", "Tag"],
        how="inner",
    )
    changed = cmp[cmp["Final_Class"] != cmp["Final_Class_Global"]]
    changed_rows = int(len(changed))
    summary["Comparison_vs_Global_Changed_Rows"] = changed_rows
    summary["Comparison_vs_Global_Changed_Tags"] = (
        int(changed["Tag"].nunique()) if changed_rows else 0
    )
    summary["Comparison_vs_Global_Changed_Row_Rate"] = (
        round(changed_rows / max(1, len(cmp)), 6) if len(cmp) else 0.0
    )
    summary["Comparison_vs_Global_Note"] = (
        "Global baseline = all timestamps (std limits), vs V4 clean-like limits + spike/change logic."
    )

    tag_limits_by_tag: Dict[str, Dict[str, Any]] = {}
    for _, r in limits_df.iterrows():
        tag = str(r.get("Tag") or "").strip()
        if not tag:
            continue
        lo_f = _safe_float(r.get("Lower_Limit"))
        hi_f = _safe_float(r.get("Upper_Limit"))
        scale_f = _safe_float(r.get("Reference_Robust_Scale"))
        tag_limits_by_tag[tag] = {
            "baseline_center": _safe_float(r.get("Reference_Median")),
            "baseline_scale": scale_f,
            "drift_lower_limit": lo_f,
            "drift_upper_limit": hi_f,
            "drift_anomaly_lower_limit": lo_f,
            "drift_anomaly_upper_limit": hi_f,
            "strong_anomaly_lower_limit": lo_f,
            "strong_anomaly_upper_limit": hi_f,
        }

    x_variables_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    try:
        corr = pivot.corr(numeric_only=True)
        for tag in pivot.columns:
            if tag not in corr.columns:
                x_variables_by_tag[str(tag)] = []
                continue
            s = corr[tag].drop(labels=[tag], errors="ignore").dropna()
            s = s.reindex(s.abs().sort_values(ascending=False).index)
            x_variables_by_tag[str(tag)] = [
                {"tag": str(other), "corr": _safe_float(val)}
                for other, val in s.head(10).items()
            ]
    except Exception:
        x_variables_by_tag = {str(t): [] for t in pivot.columns}

    limits_for_model = pd.DataFrame(
        [
            {
                "Tag": str(tag),
                "Baseline_Center": float(
                    (tag_limits_by_tag[str(tag)].get("baseline_center") or 0.0)
                ),
                "Baseline_Scale": float(
                    tag_limits_by_tag[str(tag)].get("baseline_scale") or 1e-9
                ),
                "Drift_Lower_Limit": float(
                    (tag_limits_by_tag[str(tag)].get("drift_lower_limit") or 0.0)
                ),
                "Drift_Upper_Limit": float(
                    (tag_limits_by_tag[str(tag)].get("drift_upper_limit") or 0.0)
                ),
            }
            for tag in tag_cols
            if str(tag) in tag_limits_by_tag
        ]
    )
    prediction_wide, model_meta = _build_prediction_models_per_tag(
        pivot, limits_for_model, x_variables_by_tag
    )
    model_lr_tags = sum(
        1
        for m in model_meta.values()
        if str(m.get("model_type")) == "linear_regression"
    )
    summary["Model_Built_Tags"] = model_lr_tags
    summary["Model_Fallback_Tags"] = max(0, len(model_meta) - model_lr_tags)

    abnormal = result_df[result_df["Final_Status"] == "Abnormal"].copy()
    if abnormal.empty:
        wide_plot, out_df = _build_plot_inputs(result_df)
        return {
            "summary": summary,
            "top_tags_by_points": [],
            "tag_summaries": [],
            "details_by_tag": {},
            "monthly_pages_by_tag": {},
            "df_for_script": wide_plot,
            "out_df": out_df,
            "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
            "tag_limits_by_tag": tag_limits_by_tag,
            "x_variables_by_tag": x_variables_by_tag,
        }

    tag_summaries: List[Dict[str, Any]] = []
    details_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    monthly_pages_by_tag: Dict[str, List[Dict[str, Any]]] = {}

    for tag, tag_rows in abnormal.groupby("Tag", dropna=True):
        tag_rows = tag_rows.sort_values("Timestamp")
        first = tag_rows.iloc[0]
        tag_summaries.append(
            {
                "tag": str(tag),
                "status": str(first.get("Final_Class") or ""),
                "drift_timestamp": _format_ts(first.get("Timestamp")),
                "num_drift_points": int(len(tag_rows)),
            }
        )

        all_rows = result_df[result_df["Tag"] == tag].copy()
        all_rows = all_rows.sort_values("Timestamp", ascending=True)
        if str(tag) in prediction_wide.columns:
            idx_ts = pd.to_datetime(all_rows["Timestamp"], errors="coerce")
            all_rows["Predicted_Value"] = prediction_wide[str(tag)].reindex(idx_ts).to_numpy()
        else:
            actual_num = pd.to_numeric(all_rows["Actual_Value"], errors="coerce")
            pred = actual_num.ewm(span=12, adjust=False, min_periods=1).mean().shift(1)
            pred = pred.fillna(pd.to_numeric(all_rows["Baseline_Center"], errors="coerce"))
            all_rows["Predicted_Value"] = pred
        all_rows = all_rows.sort_values("Timestamp", ascending=False)
        details_by_tag[str(tag)] = [
            {
                "Timestamp": _format_ts(r.get("Timestamp")),
                "Actual_Value": _safe_float(r.get("Actual_Value")),
                "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                "Final_Class": r.get("Final_Class"),
                "Direction": r.get("Direction"),
                "Reason": _build_reason(
                    r.get("Final_Class"), r.get("Direction"), r.get("Limit_Crossed")
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
                            "Final_Class": r.get("Final_Class"),
                            "Direction": r.get("Direction"),
                            "Reason": _build_reason(
                                r.get("Final_Class"),
                                r.get("Direction"),
                                r.get("Limit_Crossed"),
                            ),
                        }
                        for _, r in month_rows.iterrows()
                    ],
                }
            )
        monthly_pages_by_tag[str(tag)] = pages

    top_tags_by_points = sorted(
        tag_summaries,
        key=lambda r: int(r.get("num_drift_points") or 0),
        reverse=True,
    )[:10]

    wide_plot, out_df = _build_plot_inputs(result_df)

    return {
        "summary": summary,
        "top_tags_by_points": top_tags_by_points,
        "tag_summaries": tag_summaries,
        "details_by_tag": details_by_tag,
        "monthly_pages_by_tag": monthly_pages_by_tag,
        "df_for_script": wide_plot,
        "out_df": out_df,
        "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
        "tag_limits_by_tag": tag_limits_by_tag,
        "x_variables_by_tag": x_variables_by_tag,
    }


def _run_deviation_spike_v5_testing_pipeline(
    file_path: str,
    *,
    shutdown_indicator_tags: Optional[Sequence[str]] = None,
    critical_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Outlier detection tab (part8): without_causal_clean_deviation_spike_change_v5.py
    Clean period without moving average; clean-like limits; outside- and
    within-limit spike/change/persistent deviation. Same UI bundle as part5/part7.
    Global comparison = full-timeline std baseline (same as part5/part7).
    """
    import without_causal_clean_deviation_spike_change_v5 as v5

    module = _load_auto_without_causal_module()
    raw_df, selected_sheet = module.read_input_file(
        file_path, sheet_name=None, max_rows=None, datetime_format=None
    )
    timestamp_col = module.detect_timestamp_col(raw_df, override=None, datetime_format=None)
    tag_cols_arg = module.parse_tag_cols_argument(None)
    long_df, input_format, detected_ts_col, detected_tag_col, detected_value_col = (
        module.make_long_format(
            raw_df,
            timestamp_col=timestamp_col,
            tag_col=None,
            value_col=None,
            tag_cols=tag_cols_arg,
            datetime_format=None,
        )
    )
    pivot = module.build_pivot(long_df)
    if pivot.shape[1] == 0:
        raise ValueError("No tag columns after pivot; check input format.")

    ts_name = "Timestamp"
    wide = pivot.reset_index()
    wide[ts_name] = pd.to_datetime(wide[ts_name], errors="coerce")
    wide = wide.dropna(subset=[ts_name]).sort_values(ts_name).reset_index(drop=True)
    tag_cols = [c for c in wide.columns if c != ts_name]
    for c in tag_cols:
        wide[c] = pd.to_numeric(wide[c], errors="coerce")

    shutdown_set: set[str] = set()
    if shutdown_indicator_tags:
        shutdown_set = {
            str(t).strip()
            for t in shutdown_indicator_tags
            if t is not None and str(t).strip()
        }
        shutdown_set = {t for t in shutdown_set if t in wide.columns}

    rows_before_shutdown = len(wide)
    removed_shutdown = 0
    if shutdown_set:
        is_shut = wide_rows_plant_indicator_off(wide, sorted(shutdown_set))
        wide = wide.loc[~is_shut].reset_index(drop=True)
        removed_shutdown = rows_before_shutdown - len(wide)

    tag_cols = [c for c in wide.columns if c != ts_name]

    if wide.empty:
        raise ValueError(
            "All rows were removed after plant-status filtering (indicator at/near zero). "
            "Adjust plant-status tags or check the spreadsheet."
        )
    if not tag_cols:
        raise ValueError("No tag columns left after plant-status filtering.")

    long_df = wide.melt(
        id_vars=[ts_name],
        value_vars=tag_cols,
        var_name="Tag",
        value_name="Actual_Value",
    )
    long_df = long_df.rename(columns={ts_name: "Timestamp"})
    long_df["Timestamp"] = module.safe_to_datetime(
        long_df["Timestamp"], datetime_format=None
    )
    long_df["Tag"] = long_df["Tag"].astype(str).str.strip()
    long_df["Actual_Value"] = pd.to_numeric(long_df["Actual_Value"], errors="coerce")
    long_df = long_df.dropna(subset=["Timestamp", "Tag", "Actual_Value"])
    long_df = long_df[long_df["Tag"].str.lower().ne("nan")]
    long_df = long_df.sort_values(["Timestamp", "Tag"]).reset_index(drop=True)
    if long_df.empty:
        raise ValueError("No valid rows after shutdown filtering.")

    pivot = module.build_pivot(long_df)
    if pivot.shape[1] == 0:
        raise ValueError("No tag columns after shutdown filtering.")

    wide = pivot.reset_index()
    wide[ts_name] = pd.to_datetime(wide[ts_name], errors="coerce")
    wide = wide.dropna(subset=[ts_name]).sort_values(ts_name).reset_index(drop=True)
    tag_cols = [c for c in wide.columns if c != ts_name]
    for c in tag_cols:
        wide[c] = pd.to_numeric(wide[c], errors="coerce")

    cfg = v5.CONFIG.copy()
    clean_df, clean_info, clean_score_df = v5.detect_clean_period_no_mavg(
        wide, tag_cols, ts_name, cfg
    )
    limits_df = v5.build_clean_like_limits(
        wide, clean_df, clean_score_df, tag_cols, ts_name, cfg
    )
    all_results = v5.generate_without_causal_all_results(
        wide, tag_cols, ts_name, limits_df, cfg
    )
    # Remove any long-form rows for timestamps not present in post-shutdown wide data (plant-off rows).
    all_results = filter_rows_to_wide_timestamps(
        all_results, wide, ts_col="Timestamp", wide_ts_col=ts_name
    )

    result_df = all_results.copy()
    result_df["Timestamp"] = pd.to_datetime(result_df["Timestamp"], errors="coerce")
    result_df["Limit_Crossed"] = np.where(
        result_df["Limit_Status"].astype(str) == "Within Limit",
        "Within_Limits",
        result_df["Limit_Status"].astype(str),
    )
    result_df["Abs_Z"] = pd.to_numeric(result_df["Abs_Value_Z"], errors="coerce")
    result_df["Baseline_Center"] = pd.to_numeric(
        result_df["Reference_Median"], errors="coerce"
    )

    timestamp_summary = module.build_timestamp_summary(result_df)
    summary_df = v5.create_summary(wide, tag_cols, clean_info, all_results, None)
    summary: Dict[str, Any] = {
        str(r["Metric"]): r["Value"] for _, r in summary_df.iterrows()
    }
    summary["Threshold_Mode"] = (
        "Deviation / spike / change V5 (without_causal_clean_deviation_spike_change_v5)"
    )
    summary["Selected_Excel_Sheet"] = selected_sheet if selected_sheet is not None else ""
    summary["Input_Format_Detected"] = input_format
    summary["Detected_Timestamp_Column"] = detected_ts_col
    summary["Detected_Tag_Column"] = detected_tag_col
    summary["Detected_Value_Column"] = detected_value_col
    summary["Drift_Z"] = cfg["drift_z"]
    summary["Drift_Anomaly_Z"] = cfg["drift_anomaly_z"]
    summary["Strong_Anomaly_Z"] = cfg["strong_anomaly_z"]
    if shutdown_set:
        summary["Shutdown_Filter_Tags"] = ", ".join(sorted(shutdown_set))
        summary["Shutdown_Rows_Removed"] = int(removed_shutdown)
    if critical_tags:
        crit_lbl = {
            str(t).strip()
            for t in critical_tags
            if t is not None and str(t).strip()
        } & set(tag_cols)
        if crit_lbl:
            summary["Critical_Tags_Display_Only"] = ", ".join(sorted(crit_lbl))

    clean_start = pd.Series(pivot.index).min()
    clean_end = pd.Series(pivot.index).max()
    global_limits_df = module.calculate_clean_limits(
        long_df,
        clean_timestamps=pivot.index,
        baseline_method="std",
        min_clean_points=30,
    )
    global_limits_df = module.add_threshold_columns(
        global_limits_df,
        drift_z=float(cfg["drift_z"]),
        drift_anomaly_z=float(cfg["drift_anomaly_z"]),
        strong_z=float(cfg["strong_anomaly_z"]),
    )
    global_result_df = module.classify_results(
        long_df,
        global_limits_df,
        clean_timestamps=pivot.index,
        clean_period_start=clean_start,
        clean_period_end=clean_end,
    )
    cmp = result_df[["Timestamp", "Tag", "Final_Class"]].merge(
        global_result_df[["Timestamp", "Tag", "Final_Class"]].rename(
            columns={"Final_Class": "Final_Class_Global"}
        ),
        on=["Timestamp", "Tag"],
        how="inner",
    )
    changed = cmp[cmp["Final_Class"] != cmp["Final_Class_Global"]]
    changed_rows = int(len(changed))
    summary["Comparison_vs_Global_Changed_Rows"] = changed_rows
    summary["Comparison_vs_Global_Changed_Tags"] = (
        int(changed["Tag"].nunique()) if changed_rows else 0
    )
    summary["Comparison_vs_Global_Changed_Row_Rate"] = (
        round(changed_rows / max(1, len(cmp)), 6) if len(cmp) else 0.0
    )
    summary["Comparison_vs_Global_Note"] = (
        "Global baseline = all timestamps (std limits), vs V5 clean-like limits "
        "(no moving average, within-limit spike/change/deviation)."
    )

    tag_limits_by_tag: Dict[str, Dict[str, Any]] = {}
    for _, r in limits_df.iterrows():
        tag = str(r.get("Tag") or "").strip()
        if not tag:
            continue
        lo_f = _safe_float(r.get("Lower_Limit"))
        hi_f = _safe_float(r.get("Upper_Limit"))
        scale_f = _safe_float(r.get("Reference_Scale_MAD"))
        tag_limits_by_tag[tag] = {
            "baseline_center": _safe_float(r.get("Reference_Median")),
            "baseline_scale": scale_f,
            "drift_lower_limit": lo_f,
            "drift_upper_limit": hi_f,
            "drift_anomaly_lower_limit": lo_f,
            "drift_anomaly_upper_limit": hi_f,
            "strong_anomaly_lower_limit": lo_f,
            "strong_anomaly_upper_limit": hi_f,
        }

    x_variables_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    try:
        corr = pivot.corr(numeric_only=True)
        for tag in pivot.columns:
            if tag not in corr.columns:
                x_variables_by_tag[str(tag)] = []
                continue
            s = corr[tag].drop(labels=[tag], errors="ignore").dropna()
            s = s.reindex(s.abs().sort_values(ascending=False).index)
            x_variables_by_tag[str(tag)] = [
                {"tag": str(other), "corr": _safe_float(val)}
                for other, val in s.head(10).items()
            ]
    except Exception:
        x_variables_by_tag = {str(t): [] for t in pivot.columns}

    limits_for_model = pd.DataFrame(
        [
            {
                "Tag": str(tag),
                "Baseline_Center": float(
                    (tag_limits_by_tag[str(tag)].get("baseline_center") or 0.0)
                ),
                "Baseline_Scale": float(
                    tag_limits_by_tag[str(tag)].get("baseline_scale") or 1e-9
                ),
                "Drift_Lower_Limit": float(
                    (tag_limits_by_tag[str(tag)].get("drift_lower_limit") or 0.0)
                ),
                "Drift_Upper_Limit": float(
                    (tag_limits_by_tag[str(tag)].get("drift_upper_limit") or 0.0)
                ),
            }
            for tag in tag_cols
            if str(tag) in tag_limits_by_tag
        ]
    )
    prediction_wide, model_meta = _build_prediction_models_per_tag(
        pivot, limits_for_model, x_variables_by_tag
    )
    model_lr_tags = sum(
        1
        for m in model_meta.values()
        if str(m.get("model_type")) == "linear_regression"
    )
    summary["Model_Built_Tags"] = model_lr_tags
    summary["Model_Fallback_Tags"] = max(0, len(model_meta) - model_lr_tags)

    abnormal = result_df[result_df["Final_Status"] == "Abnormal"].copy()
    if abnormal.empty:
        wide_plot, out_df = _build_plot_inputs(result_df)
        if shutdown_set:
            wide_plot, out_df = clip_plot_inputs_to_wide_timestamps(
                wide_plot, out_df, wide, ts_name=ts_name
            )
            summary["Plant_Off_Treat_As_Max_Value"] = DEFAULT_PLANT_OFF_MAX_VALUE
        out_empty: Dict[str, Any] = {
            "summary": summary,
            "top_tags_by_points": [],
            "tag_summaries": [],
            "details_by_tag": {},
            "monthly_pages_by_tag": {},
            "df_for_script": wide_plot,
            "out_df": out_df,
            "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
            "tag_limits_by_tag": tag_limits_by_tag,
            "x_variables_by_tag": x_variables_by_tag,
        }
        _v5_apply_critical_display_filter(
            out_empty, tag_cols=tag_cols, critical_tags=critical_tags
        )
        return out_empty

    tag_summaries: List[Dict[str, Any]] = []
    details_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    monthly_pages_by_tag: Dict[str, List[Dict[str, Any]]] = {}

    for tag, tag_rows in abnormal.groupby("Tag", dropna=True):
        tag_rows = tag_rows.sort_values("Timestamp")
        first = tag_rows.iloc[0]
        tag_summaries.append(
            {
                "tag": str(tag),
                "status": str(first.get("Final_Class") or ""),
                "drift_timestamp": _format_ts(first.get("Timestamp")),
                "num_drift_points": int(len(tag_rows)),
            }
        )

        all_rows = result_df[result_df["Tag"] == tag].copy()
        all_rows = all_rows.sort_values("Timestamp", ascending=True)
        if str(tag) in prediction_wide.columns:
            idx_ts = pd.to_datetime(all_rows["Timestamp"], errors="coerce")
            all_rows["Predicted_Value"] = prediction_wide[str(tag)].reindex(idx_ts).to_numpy()
        else:
            actual_num = pd.to_numeric(all_rows["Actual_Value"], errors="coerce")
            pred = actual_num.ewm(span=12, adjust=False, min_periods=1).mean().shift(1)
            pred = pred.fillna(pd.to_numeric(all_rows["Baseline_Center"], errors="coerce"))
            all_rows["Predicted_Value"] = pred
        all_rows = all_rows.sort_values("Timestamp", ascending=False)
        details_by_tag[str(tag)] = [
            {
                "Timestamp": _format_ts(r.get("Timestamp")),
                "Actual_Value": _safe_float(r.get("Actual_Value")),
                "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                "Final_Class": r.get("Final_Class"),
                "Direction": r.get("Direction"),
                "Reason": _build_reason(
                    r.get("Final_Class"), r.get("Direction"), r.get("Limit_Crossed")
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
                            "Final_Class": r.get("Final_Class"),
                            "Direction": r.get("Direction"),
                            "Reason": _build_reason(
                                r.get("Final_Class"),
                                r.get("Direction"),
                                r.get("Limit_Crossed"),
                            ),
                        }
                        for _, r in month_rows.iterrows()
                    ],
                }
            )
        monthly_pages_by_tag[str(tag)] = pages

    top_tags_by_points = sorted(
        tag_summaries,
        key=lambda r: int(r.get("num_drift_points") or 0),
        reverse=True,
    )[:10]

    wide_plot, out_df = _build_plot_inputs(result_df)
    if shutdown_set:
        wide_plot, out_df = clip_plot_inputs_to_wide_timestamps(
            wide_plot, out_df, wide, ts_name=ts_name
        )

    out = {
        "summary": summary,
        "top_tags_by_points": top_tags_by_points,
        "tag_summaries": tag_summaries,
        "details_by_tag": details_by_tag,
        "monthly_pages_by_tag": monthly_pages_by_tag,
        "df_for_script": wide_plot,
        "out_df": out_df,
        "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
        "tag_limits_by_tag": tag_limits_by_tag,
        "x_variables_by_tag": x_variables_by_tag,
    }
    if shutdown_set:
        summary["Plant_Off_Treat_As_Max_Value"] = DEFAULT_PLANT_OFF_MAX_VALUE
    _v5_apply_critical_display_filter(
        out, tag_cols=tag_cols, critical_tags=critical_tags
    )
    return out


def run_auto_without_causal_outlier_drift(file_path: str) -> Dict[str, Any]:
    """
    Auto (No Causal): uses auto-detected clean reference period.
    """
    return _run_without_causal_pipeline(
        file_path,
        use_auto_clean_reference=True,
        threshold_mode="global_default",
    )


def run_without_clean_data_outlier_drift(file_path: str) -> Dict[str, Any]:
    """
    Auto (No Clean Data): auto stable window + clean-window-only limits
    (without_causal_auto_clean_outlier); comparison baseline = full timeline.
    """
    return _run_auto_clean_outlier_pipeline(file_path)


def run_auto_identification_outlier_drift(file_path: str) -> Dict[str, Any]:
    """
    Auto Identification: clean-anchored similar-history limits + persistence
    (without_causal_clean_anchored_improved_v3).
    """
    return _run_clean_anchored_v3_identification_pipeline(file_path)


def run_testing_deviation_spike_v4_outlier_drift(file_path: str) -> Dict[str, Any]:
    """
    Testing: deviation / spike / change clean window + clean-like limits (V4).
    """
    return _run_deviation_spike_v4_testing_pipeline(file_path)


def run_testing_deviation_spike_v5_outlier_drift(
    file_path: str,
    *,
    shutdown_indicator_tags: Optional[Sequence[str]] = None,
    critical_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Outlier detection: no moving average + within-limit spike/change/deviation
    (without_causal_clean_deviation_spike_change_v5).
    """
    return _run_deviation_spike_v5_testing_pipeline(
        file_path,
        shutdown_indicator_tags=shutdown_indicator_tags,
        critical_tags=critical_tags,
    )


def _load_top5_corr_workbook(
    file_path: str, t5_mod: Any
) -> Tuple[pd.DataFrame, str, List[str], str | None]:
    """Build wide tag matrix the same way as part8 (best sheet, headers, long or wide)."""
    _ = t5_mod  # call sites pass top5 module; loading uses auto_without_causal script helpers
    module = _load_auto_without_causal_module()
    raw_df, selected_sheet = module.read_input_file(
        file_path, sheet_name=None, max_rows=None, datetime_format=None
    )
    if raw_df.empty:
        raise ValueError("Selected sheet is empty.")
    timestamp_col = module.detect_timestamp_col(raw_df, override=None, datetime_format=None)
    tag_cols_arg = module.parse_tag_cols_argument(None)
    long_df, _input_format, _dts, _dtag, _dval = module.make_long_format(
        raw_df,
        timestamp_col=timestamp_col,
        tag_col=None,
        value_col=None,
        tag_cols=tag_cols_arg,
        datetime_format=None,
    )
    pivot = module.build_pivot(long_df)
    if pivot.shape[1] == 0:
        raise ValueError("No tag columns after pivot; check input format.")

    ts_name = "Timestamp"
    wide = pivot.reset_index()
    wide[ts_name] = pd.to_datetime(wide[ts_name], errors="coerce")
    wide = wide.dropna(subset=[ts_name]).sort_values(ts_name).reset_index(drop=True)

    tag_cols: List[str] = []
    for c in wide.columns:
        if c == ts_name:
            continue
        x = pd.to_numeric(wide[c], errors="coerce")
        if x.notna().sum() >= 5:
            wide[c] = x
            tag_cols.append(c)
    if len(tag_cols) < 2:
        tag_cols = []
        for c in wide.columns:
            if c == ts_name:
                continue
            x = pd.to_numeric(wide[c], errors="coerce")
            if x.notna().sum() > 0:
                wide[c] = x
                tag_cols.append(c)
    if len(tag_cols) < 2:
        raise ValueError(
            "Need at least two numeric tag columns. "
            "Use a wide sheet (time + tag columns) or long format (time, tag name, value)."
        )
    return wide, ts_name, tag_cols, selected_sheet


def _run_top5_corr_regression_testing_pipeline(file_path: str) -> Dict[str, Any]:
    """
    Outlier detection (using data model) tab (part9): without_causal_top5_corr_regression_fast.py
    Top correlated tags per target, ridge prediction, residual/value/peer rules.
    Same results UI bundle as part7/part8.
    """
    import contextlib
    import io

    import without_causal_top5_corr_regression_fast as t5

    config = dict(t5.DEFAULTS)
    df, ts, tag_cols, sheet_used = _load_top5_corr_workbook(file_path, t5)

    with contextlib.redirect_stdout(io.StringIO()):
        all_results, tag_summary_df, _event, _anom, stable_rows = t5.run_model(
            df, ts, tag_cols, config
        )

    result_df = all_results.copy()
    result_df["Timestamp"] = pd.to_datetime(result_df["Timestamp"], errors="coerce")
    pred = pd.to_numeric(
        result_df["Predicted_Value_From_Related_Tags"], errors="coerce"
    )
    result_df["Predicted_Value"] = pred
    actual = pd.to_numeric(result_df["Actual_Value"], errors="coerce")
    result_df["Direction"] = np.where(
        actual >= pred, "High", np.where(actual < pred, "Low", "Unknown")
    )
    result_df["Limit_Crossed"] = np.where(
        result_df["Outer_Range_Flag"].astype(bool),
        "Outer_Range",
        np.where(
            result_df["Soft_Range_Flag"].astype(bool), "Soft_Range", "Within_Limits"
        ),
    )
    result_df["Limit_Status"] = result_df["Limit_Crossed"]
    vz = pd.to_numeric(result_df["Value_Z"], errors="coerce")
    result_df["Abs_Value_Z"] = vz.abs()
    result_df["Abs_Z"] = result_df["Abs_Value_Z"]
    lo5 = pd.to_numeric(result_df["Historical_Low_5pct"], errors="coerce")
    hi95 = pd.to_numeric(result_df["Historical_High_95pct"], errors="coerce")
    result_df["Reference_Median"] = (lo5 + hi95) / 2.0
    result_df["Baseline_Center"] = result_df["Reference_Median"]

    summary_df = t5.make_summary(
        all_results, tag_summary_df, stable_rows, config, comp=None
    )
    summary: Dict[str, Any] = {
        str(r["Metric"]): r["Value"] for _, r in summary_df.iterrows()
    }
    summary["Threshold_Mode"] = (
        "Top-5 correlated ridge regression (without_causal_top5_corr_regression_fast)"
    )
    summary["Selected_Excel_Sheet"] = sheet_used if sheet_used is not None else ""

    module = _load_auto_without_causal_module()
    timestamp_summary = module.build_timestamp_summary(result_df)

    tag_limits_by_tag: Dict[str, Dict[str, Any]] = {}
    for tag in tag_cols:
        sub = result_df[result_df["Tag"].astype(str) == str(tag)]
        if sub.empty:
            continue
        r0 = sub.iloc[0]
        lo5_f = _safe_float(r0.get("Historical_Low_5pct"))
        hi95_f = _safe_float(r0.get("Historical_High_95pct"))
        lo1_f = _safe_float(r0.get("Historical_Low_1pct"))
        hi99_f = _safe_float(r0.get("Historical_High_99pct"))
        ctr = None
        if lo5_f is not None and hi95_f is not None:
            ctr = (lo5_f + hi95_f) / 2.0
        scale = 1e-9
        if lo5_f is not None and hi95_f is not None and hi95_f > lo5_f:
            scale = max((hi95_f - lo5_f) / 3.5, 1e-9)
        tag_limits_by_tag[str(tag)] = {
            "baseline_center": ctr,
            "baseline_scale": scale,
            "drift_lower_limit": lo5_f,
            "drift_upper_limit": hi95_f,
            "drift_anomaly_lower_limit": lo1_f,
            "drift_anomaly_upper_limit": hi99_f,
            "strong_anomaly_lower_limit": lo1_f,
            "strong_anomaly_upper_limit": hi99_f,
        }

    x_variables_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    for _, tr in tag_summary_df.iterrows():
        tag = str(tr.get("Tag") or "").strip()
        if not tag:
            continue
        parts = str(tr.get("Top_Correlations") or "").split(", ")
        xs: List[Dict[str, Any]] = []
        for p in parts:
            if ":" in p:
                nm, corr_s = p.split(":", 1)
                c = _safe_float(corr_s.strip())
                xs.append({"tag": nm.strip(), "corr": c})
        x_variables_by_tag[tag] = xs

    abnormal = result_df[result_df["Final_Status"] == "Abnormal"].copy()
    if abnormal.empty:
        wide_plot, out_df = _build_plot_inputs(result_df)
        return {
            "summary": summary,
            "top_tags_by_points": [],
            "tag_summaries": [],
            "details_by_tag": {},
            "monthly_pages_by_tag": {},
            "df_for_script": wide_plot,
            "out_df": out_df,
            "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
            "tag_limits_by_tag": tag_limits_by_tag,
            "x_variables_by_tag": x_variables_by_tag,
        }

    tag_summaries: List[Dict[str, Any]] = []
    details_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    monthly_pages_by_tag: Dict[str, List[Dict[str, Any]]] = {}

    for tag, tag_rows in abnormal.groupby("Tag", dropna=True):
        tag_rows = tag_rows.sort_values("Timestamp")
        first = tag_rows.iloc[0]
        tag_summaries.append(
            {
                "tag": str(tag),
                "status": str(first.get("Final_Class") or ""),
                "drift_timestamp": _format_ts(first.get("Timestamp")),
                "num_drift_points": int(len(tag_rows)),
            }
        )

        all_rows = result_df[result_df["Tag"].astype(str) == str(tag)].copy()
        all_rows = all_rows.sort_values("Timestamp", ascending=True)
        all_rows = all_rows.sort_values("Timestamp", ascending=False)
        details_by_tag[str(tag)] = [
            {
                "Timestamp": _format_ts(r.get("Timestamp")),
                "Actual_Value": _safe_float(r.get("Actual_Value")),
                "Predicted_Value": _safe_float(r.get("Predicted_Value")),
                "Final_Class": r.get("Final_Class"),
                "Direction": r.get("Direction"),
                "Reason": _build_reason(
                    r.get("Final_Class"), r.get("Direction"), r.get("Limit_Crossed")
                ),
            }
            for _, r in all_rows.iterrows()
        ]

        tmp = all_rows.copy()
        tmp["month_key"] = (
            pd.to_datetime(tmp["Timestamp"], errors="coerce")
            .dt.to_period("M")
            .astype(str)
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
                            "Final_Class": r.get("Final_Class"),
                            "Direction": r.get("Direction"),
                            "Reason": _build_reason(
                                r.get("Final_Class"),
                                r.get("Direction"),
                                r.get("Limit_Crossed"),
                            ),
                        }
                        for _, r in month_rows.iterrows()
                    ],
                }
            )
        monthly_pages_by_tag[str(tag)] = pages

    top_tags_by_points = sorted(
        tag_summaries,
        key=lambda r: int(r.get("num_drift_points") or 0),
        reverse=True,
    )[:10]

    wide_plot, out_df = _build_plot_inputs(result_df)

    return {
        "summary": summary,
        "top_tags_by_points": top_tags_by_points,
        "tag_summaries": tag_summaries,
        "details_by_tag": details_by_tag,
        "monthly_pages_by_tag": monthly_pages_by_tag,
        "df_for_script": wide_plot,
        "out_df": out_df,
        "timestamp_summary_rows": timestamp_summary.to_dict(orient="records"),
        "tag_limits_by_tag": tag_limits_by_tag,
        "x_variables_by_tag": x_variables_by_tag,
    }


def run_testing_top5_corr_regression_outlier_drift(file_path: str) -> Dict[str, Any]:
    """Outlier detection (using data model): top-5 correlated ridge (without_causal_top5_corr_regression_fast)."""
    return _run_top5_corr_regression_testing_pipeline(file_path)


def get_no_causal_auto_classify_long_df(file_path: str) -> pd.DataFrame:
    """
    Part4-style classification only (auto clean window + std limits + fixed z ladder).
    Used by Testing V7 fusion.
    """
    module = _load_auto_without_causal_module()
    raw_df, _selected_sheet = module.read_input_file(
        file_path, sheet_name=None, max_rows=None, datetime_format=None
    )
    timestamp_col = module.detect_timestamp_col(raw_df, override=None, datetime_format=None)
    tag_cols_arg = module.parse_tag_cols_argument(None)
    long_df, _input_format, _dts, _dtag, _dval = module.make_long_format(
        raw_df,
        timestamp_col=timestamp_col,
        tag_col=None,
        value_col=None,
        tag_cols=tag_cols_arg,
        datetime_format=None,
    )
    pivot = module.build_pivot(long_df)
    clean_timestamps, _clean_diag = module.auto_detect_clean_timestamps(
        pivot,
        clean_window_fraction=0.15,
        min_clean_points=30,
        clean_trim_quantile=0.85,
        max_clean_fraction=0.35,
    )
    clean_start = pd.Series(clean_timestamps).min()
    clean_end = pd.Series(clean_timestamps).max()
    limits_df = module.calculate_clean_limits(
        long_df,
        clean_timestamps=clean_timestamps,
        baseline_method="std",
        min_clean_points=30,
    )
    limits_df = module.add_threshold_columns(
        limits_df,
        drift_z=3.0,
        drift_anomaly_z=3.5,
        strong_z=5.0,
    )
    result_df = module.classify_results(
        long_df,
        limits_df,
        clean_timestamps=clean_timestamps,
        clean_period_start=clean_start,
        clean_period_end=clean_end,
    )
    return result_df


def get_no_causal_auto_limits_df(file_path: str) -> pd.DataFrame:
    """Limits DataFrame from same Part4 path as get_no_causal_auto_classify_long_df."""
    module = _load_auto_without_causal_module()
    raw_df, _ = module.read_input_file(
        file_path, sheet_name=None, max_rows=None, datetime_format=None
    )
    timestamp_col = module.detect_timestamp_col(raw_df, override=None, datetime_format=None)
    tag_cols_arg = module.parse_tag_cols_argument(None)
    long_df, _, _, _, _ = module.make_long_format(
        raw_df,
        timestamp_col=timestamp_col,
        tag_col=None,
        value_col=None,
        tag_cols=tag_cols_arg,
        datetime_format=None,
    )
    pivot = module.build_pivot(long_df)
    clean_timestamps, _ = module.auto_detect_clean_timestamps(
        pivot,
        clean_window_fraction=0.15,
        min_clean_points=30,
        clean_trim_quantile=0.85,
        max_clean_fraction=0.35,
    )
    clean_start = pd.Series(clean_timestamps).min()
    clean_end = pd.Series(clean_timestamps).max()
    limits_df = module.calculate_clean_limits(
        long_df,
        clean_timestamps=clean_timestamps,
        baseline_method="std",
        min_clean_points=30,
    )
    return module.add_threshold_columns(
        limits_df,
        drift_z=3.0,
        drift_anomaly_z=3.5,
        strong_z=5.0,
    )


def get_testing_v5_classify_long_df(file_path: str) -> pd.DataFrame:
    """Testing V5 long result rows (deviation / spike / change), for fusion."""
    import without_causal_clean_deviation_spike_change_v5 as v5

    module = _load_auto_without_causal_module()
    raw_df, _selected_sheet = module.read_input_file(
        file_path, sheet_name=None, max_rows=None, datetime_format=None
    )
    timestamp_col = module.detect_timestamp_col(raw_df, override=None, datetime_format=None)
    tag_cols_arg = module.parse_tag_cols_argument(None)
    long_df, _, _, _, _ = module.make_long_format(
        raw_df,
        timestamp_col=timestamp_col,
        tag_col=None,
        value_col=None,
        tag_cols=tag_cols_arg,
        datetime_format=None,
    )
    pivot = module.build_pivot(long_df)
    if pivot.shape[1] == 0:
        raise ValueError("No tag columns after pivot; check input format.")
    wide = pivot.reset_index()
    ts_name = "Timestamp"
    wide[ts_name] = pd.to_datetime(wide[ts_name], errors="coerce")
    wide = wide.dropna(subset=[ts_name]).sort_values(ts_name).reset_index(drop=True)
    tag_cols = [c for c in wide.columns if c != ts_name]
    for c in tag_cols:
        wide[c] = pd.to_numeric(wide[c], errors="coerce")
    cfg = v5.CONFIG.copy()
    clean_df, _clean_info, clean_score_df = v5.detect_clean_period_no_mavg(
        wide, tag_cols, ts_name, cfg
    )
    limits_df = v5.build_clean_like_limits(
        wide, clean_df, clean_score_df, tag_cols, ts_name, cfg
    )
    all_results = v5.generate_without_causal_all_results(
        wide, tag_cols, ts_name, limits_df, cfg
    )
    result_df = all_results.copy()
    result_df["Timestamp"] = pd.to_datetime(result_df["Timestamp"], errors="coerce")
    result_df["Limit_Crossed"] = np.where(
        result_df["Limit_Status"].astype(str) == "Within Limit",
        "Within_Limits",
        result_df["Limit_Status"].astype(str),
    )
    result_df["Abs_Z"] = pd.to_numeric(result_df["Abs_Value_Z"], errors="coerce")
    result_df["Baseline_Center"] = pd.to_numeric(
        result_df["Reference_Median"], errors="coerce"
    )
    return result_df


def get_testing_v5_classify_long_df_from_wide(
    wide: pd.DataFrame, timestamp_col: str = "Timestamp"
) -> pd.DataFrame:
    """Same Testing V5 long rows as ``get_testing_v5_classify_long_df``, but from a wide DataFrame."""
    import without_causal_clean_deviation_spike_change_v5 as v5

    wide_df = wide.copy()
    if timestamp_col not in wide_df.columns:
        raise ValueError(f"Wide frame must include {timestamp_col!r}.")
    wide_df[timestamp_col] = pd.to_datetime(wide_df[timestamp_col], errors="coerce")
    wide_df = wide_df.dropna(subset=[timestamp_col]).sort_values(timestamp_col).reset_index(
        drop=True
    )
    tag_cols = [c for c in wide_df.columns if c != timestamp_col]
    for c in tag_cols:
        wide_df[c] = pd.to_numeric(wide_df[c], errors="coerce")
    if not tag_cols:
        raise ValueError("No tag columns after timestamp column.")
    cfg = v5.CONFIG.copy()
    clean_df, _clean_info, clean_score_df = v5.detect_clean_period_no_mavg(
        wide_df, tag_cols, timestamp_col, cfg
    )
    limits_df = v5.build_clean_like_limits(
        wide_df, clean_df, clean_score_df, tag_cols, timestamp_col, cfg
    )
    all_results = v5.generate_without_causal_all_results(
        wide_df, tag_cols, timestamp_col, limits_df, cfg
    )
    result_df = all_results.copy()
    result_df["Timestamp"] = pd.to_datetime(result_df["Timestamp"], errors="coerce")
    result_df["Limit_Crossed"] = np.where(
        result_df["Limit_Status"].astype(str) == "Within Limit",
        "Within_Limits",
        result_df["Limit_Status"].astype(str),
    )
    result_df["Abs_Z"] = pd.to_numeric(result_df["Abs_Value_Z"], errors="coerce")
    result_df["Baseline_Center"] = pd.to_numeric(
        result_df["Reference_Median"], errors="coerce"
    )
    return result_df


def get_testing_v6_top5_enriched_long_df(file_path: str) -> pd.DataFrame:
    """Testing V6 enriched long rows (ridge + residual rules), for fusion."""
    import contextlib
    import io

    import without_causal_top5_corr_regression_fast as t5

    config = dict(t5.DEFAULTS)
    df, ts, tag_cols, _sheet_used = _load_top5_corr_workbook(file_path, t5)
    with contextlib.redirect_stdout(io.StringIO()):
        all_results, _tag_summary_df, _event, _anom, _stable_rows = t5.run_model(
            df, ts, tag_cols, config
        )
    result_df = all_results.copy()
    result_df["Timestamp"] = pd.to_datetime(result_df["Timestamp"], errors="coerce")
    pred = pd.to_numeric(
        result_df["Predicted_Value_From_Related_Tags"], errors="coerce"
    )
    result_df["Predicted_Value"] = pred
    actual = pd.to_numeric(result_df["Actual_Value"], errors="coerce")
    result_df["Direction"] = np.where(
        actual >= pred, "High", np.where(actual < pred, "Low", "Unknown")
    )
    result_df["Limit_Crossed"] = np.where(
        result_df["Outer_Range_Flag"].astype(bool),
        "Outer_Range",
        np.where(
            result_df["Soft_Range_Flag"].astype(bool), "Soft_Range", "Within_Limits"
        ),
    )
    result_df["Limit_Status"] = result_df["Limit_Crossed"]
    vz = pd.to_numeric(result_df["Value_Z"], errors="coerce")
    result_df["Abs_Value_Z"] = vz.abs()
    result_df["Abs_Z"] = result_df["Abs_Value_Z"]
    lo5 = pd.to_numeric(result_df["Historical_Low_5pct"], errors="coerce")
    hi95 = pd.to_numeric(result_df["Historical_High_95pct"], errors="coerce")
    result_df["Reference_Median"] = (lo5 + hi95) / 2.0
    result_df["Baseline_Center"] = result_df["Reference_Median"]
    return result_df


def run_testing_fusion_v7_outlier_drift(file_path: str) -> Dict[str, Any]:
    """Testing (V7): fuse Auto no causal + outlier detection + outlier detection (using data model)."""
    from services.testing_fusion_v7 import run_testing_fusion_v7_pipeline

    return run_testing_fusion_v7_pipeline(file_path)
