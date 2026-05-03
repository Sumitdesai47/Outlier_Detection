"""
Testing (V7): consensus fusion of Auto No Causal (Part4) + Outlier detection + Outlier detection (using data model).

Designed as a cross-functional view (operations / controls / data science):
- **Part4**: clean-era univariate z-bands (simple, auditable).
- **Outlier detection**: dynamics — spikes, error-change, persistent in-band deviation.
- **Outlier detection (using data model)**: multivariate — top-5 correlated ridge residual + peer context + contextual anomaly.

Fusion policy (default): **ordinal severity union** — take the highest severity class
among the three engines on each (Timestamp, Tag) row. Missing engine label (e.g. when the
data-model tab omits a tag because no predictors exist) is treated as **Normal** for that engine so other
signals still count.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from services.auto_without_causal_outlier_drift import (
    _build_plot_inputs,
    _build_reason,
    _format_ts,
    _load_auto_without_causal_module,
    _safe_float,
    get_no_causal_auto_classify_long_df,
    get_no_causal_auto_limits_df,
    get_testing_v5_classify_long_df,
    get_testing_v6_top5_enriched_long_df,
)

# Severity order: index 0 = calm, higher = more severe. Contextual sits between Drift and Drift+Anomaly.
FUSION_CLASS_ORDER: List[str] = [
    "Normal",
    "Drift",
    "Contextual Anomaly",
    "Drift + Anomaly",
    "Strong Anomaly",
]

_FUSION_RANK: Dict[str, int] = {c: i for i, c in enumerate(FUSION_CLASS_ORDER)}


def _normalize_class(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "Normal"
    s = str(val).strip()
    if s in _FUSION_RANK:
        return s
    sl = s.lower()
    for c in FUSION_CLASS_ORDER:
        if c.lower() == sl:
            return c
    return "Normal"


def _rank_class(cls: str) -> int:
    return _FUSION_RANK.get(_normalize_class(cls), 0)


def fuse_three_labels(
    p4: Any, v5: Any, v6: Any
) -> Tuple[str, str, List[str]]:
    """
    Ordinal union: max severity across engines.
    Returns (fused_final_class, short_rationale, list of engines at max rank).
    """
    c4, c5, c6 = _normalize_class(p4), _normalize_class(v5), _normalize_class(v6)
    r4, r5, r6 = _rank_class(c4), _rank_class(c5), _rank_class(c6)
    m = max(r4, r5, r6)
    fused = FUSION_CLASS_ORDER[m]
    at_max: List[str] = []
    if r4 == m:
        at_max.append(f"Auto_no_causal={c4}")
    if r5 == m:
        at_max.append(f"Outlier_detection={c5}")
    if r6 == m:
        at_max.append(f"Data_model={c6}")
    rationale = (
        f"Fusion=max_severity({c4}|{c5}|{c6})->{fused}; drivers: "
        + ", ".join(at_max)
    )
    return fused, rationale, at_max


def _norm_keys(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["Timestamp"] = pd.to_datetime(out["Timestamp"], errors="coerce")
    out["Tag"] = out["Tag"].astype(str).str.strip()
    return out


def _limits_from_no_causal_row(lr: pd.Series) -> Dict[str, Any]:
    return {
        "baseline_center": _safe_float(lr.get("Baseline_Center")),
        "baseline_scale": _safe_float(lr.get("Baseline_Scale")),
        "drift_lower_limit": _safe_float(lr.get("Drift_Lower_Limit")),
        "drift_upper_limit": _safe_float(lr.get("Drift_Upper_Limit")),
        "drift_anomaly_lower_limit": _safe_float(lr.get("Drift_Anomaly_Lower_Limit")),
        "drift_anomaly_upper_limit": _safe_float(lr.get("Drift_Anomaly_Upper_Limit")),
        "strong_anomaly_lower_limit": _safe_float(lr.get("Strong_Anomaly_Lower_Limit")),
        "strong_anomaly_upper_limit": _safe_float(lr.get("Strong_Anomaly_Upper_Limit")),
    }


def _limits_from_v6_row(r0: pd.Series) -> Dict[str, Any]:
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
    return {
        "baseline_center": ctr,
        "baseline_scale": scale,
        "drift_lower_limit": lo5_f,
        "drift_upper_limit": hi95_f,
        "drift_anomaly_lower_limit": lo1_f,
        "drift_anomaly_upper_limit": hi99_f,
        "strong_anomaly_lower_limit": lo1_f,
        "strong_anomaly_upper_limit": hi99_f,
    }


def _xvars_from_v6_column(sample_row: pd.Series) -> List[Dict[str, Any]]:
    raw = sample_row.get("Top_Correlated_Features")
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    xs: List[Dict[str, Any]] = []
    for part in str(raw).split(","):
        part = part.strip()
        m = re.match(r"^(.+?)\s*\(\s*([-+0-9.eE]+)\s*\)\s*$", part)
        if m:
            xs.append({"tag": m.group(1).strip(), "corr": _safe_float(m.group(2))})
    return xs


def run_testing_fusion_v7_pipeline(file_path: str) -> Dict[str, Any]:
    df_p4 = _norm_keys(get_no_causal_auto_classify_long_df(file_path))
    df_v5 = _norm_keys(get_testing_v5_classify_long_df(file_path))
    df_v6 = _norm_keys(get_testing_v6_top5_enriched_long_df(file_path))
    limits_nc = get_no_causal_auto_limits_df(file_path)

    p4m = df_p4[
        ["Timestamp", "Tag", "Actual_Value", "Final_Class", "Direction", "Limit_Crossed"]
    ].rename(
        columns={
            "Actual_Value": "AV_P4",
            "Final_Class": "Final_Class_NoCausal",
            "Direction": "Direction_NoCausal",
            "Limit_Crossed": "Limit_Crossed_NoCausal",
        }
    )
    v5m = df_v5[
        ["Timestamp", "Tag", "Actual_Value", "Final_Class", "Direction", "Limit_Crossed"]
    ].rename(
        columns={
            "Actual_Value": "AV_V5",
            "Final_Class": "Final_Class_V5",
            "Direction": "Direction_V5",
            "Limit_Crossed": "Limit_Crossed_V5",
        }
    )
    v6cols = [
        "Timestamp",
        "Tag",
        "Actual_Value",
        "Final_Class",
        "Predicted_Value",
        "Direction",
        "Limit_Crossed",
        "Abs_Z",
        "Baseline_Center",
    ]
    if "Top_Correlated_Features" in df_v6.columns:
        v6cols.append("Top_Correlated_Features")
    if "Historical_Low_5pct" in df_v6.columns:
        v6cols.extend(
            [
                "Historical_Low_5pct",
                "Historical_High_95pct",
                "Historical_Low_1pct",
                "Historical_High_99pct",
            ]
        )
    v6m = df_v6[[c for c in v6cols if c in df_v6.columns]].rename(
        columns={
            "Actual_Value": "AV_V6",
            "Final_Class": "Final_Class_V6",
            "Direction": "Direction_V6",
            "Limit_Crossed": "Limit_Crossed_V6",
        }
    )

    merged = p4m.merge(v5m, on=["Timestamp", "Tag"], how="outer")
    merged = merged.merge(v6m, on=["Timestamp", "Tag"], how="outer")
    merged["Actual_Value"] = pd.to_numeric(
        merged["AV_P4"].combine_first(merged["AV_V5"]).combine_first(merged["AV_V6"]),
        errors="coerce",
    )
    merged = merged.drop(
        columns=[c for c in ["AV_P4", "AV_V5", "AV_V6"] if c in merged.columns],
        errors="ignore",
    )

    for col in (
        "Final_Class_NoCausal",
        "Final_Class_V5",
        "Final_Class_V6",
    ):
        if col not in merged.columns:
            merged[col] = "Normal"
        merged[col] = merged[col].fillna("Normal")

    fused_classes: List[str] = []
    rationales: List[str] = []
    for _, row in merged.iterrows():
        fc, rat, _ = fuse_three_labels(
            row.get("Final_Class_NoCausal"),
            row.get("Final_Class_V5"),
            row.get("Final_Class_V6"),
        )
        fused_classes.append(fc)
        rationales.append(rat)

    merged["Final_Class"] = fused_classes
    merged["Fusion_Rationale"] = rationales
    merged["Final_Status"] = np.where(
        merged["Final_Class"].eq("Normal"), "Normal", "Abnormal"
    )

    if "Predicted_Value" not in merged.columns:
        merged["Predicted_Value"] = np.nan
    else:
        merged["Predicted_Value"] = pd.to_numeric(
            merged["Predicted_Value"], errors="coerce"
        )
    fallback_dir = pd.Series("Unknown", index=merged.index)
    if "Direction_V6" in merged.columns:
        fallback_dir = merged["Direction_V6"].fillna(fallback_dir)
    if "Direction_NoCausal" in merged.columns:
        fallback_dir = merged["Direction_NoCausal"].fillna(fallback_dir)
    merged["Direction"] = np.where(
        merged["Predicted_Value"].notna(),
        np.where(merged["Actual_Value"] >= merged["Predicted_Value"], "High", "Low"),
        fallback_dir,
    )
    lc_v6 = (
        merged["Limit_Crossed_V6"]
        if "Limit_Crossed_V6" in merged.columns
        else pd.Series("Within_Limits", index=merged.index)
    )
    lc_nc = (
        merged["Limit_Crossed_NoCausal"]
        if "Limit_Crossed_NoCausal" in merged.columns
        else pd.Series("Within_Limits", index=merged.index)
    )
    merged["Limit_Crossed"] = lc_v6.combine_first(lc_nc).fillna("Within_Limits")

    merged["Abs_Z"] = pd.to_numeric(merged.get("Abs_Z"), errors="coerce")
    if merged["Abs_Z"].isna().all():
        merged["Abs_Z"] = 0.0
    merged["Baseline_Center"] = pd.to_numeric(
        merged.get("Baseline_Center"), errors="coerce"
    )

    result_df = merged.dropna(subset=["Timestamp", "Tag"]).sort_values(
        ["Timestamp", "Tag"]
    )

    module = _load_auto_without_causal_module()
    timestamp_summary = module.build_timestamp_summary(result_df)

    tag_limits_by_tag: Dict[str, Dict[str, Any]] = {}
    nc_map = limits_nc.set_index(limits_nc["Tag"].astype(str).str.strip())
    for tag in result_df["Tag"].unique():
        ts = str(tag).strip()
        sub_v6 = df_v6[df_v6["Tag"].astype(str).str.strip() == ts]
        if not sub_v6.empty:
            tag_limits_by_tag[ts] = _limits_from_v6_row(sub_v6.iloc[0])
        elif ts in nc_map.index:
            tag_limits_by_tag[ts] = _limits_from_no_causal_row(nc_map.loc[ts])
        else:
            tag_limits_by_tag[ts] = {
                "baseline_center": None,
                "baseline_scale": 1e-9,
                "drift_lower_limit": None,
                "drift_upper_limit": None,
                "drift_anomaly_lower_limit": None,
                "drift_anomaly_upper_limit": None,
                "strong_anomaly_lower_limit": None,
                "strong_anomaly_upper_limit": None,
            }

    x_variables_by_tag: Dict[str, List[Dict[str, Any]]] = {}
    if "Top_Correlated_Features" in df_v6.columns:
        for tag in result_df["Tag"].unique():
            ts = str(tag).strip()
            sub = df_v6[df_v6["Tag"].astype(str).str.strip() == ts]
            x_variables_by_tag[ts] = (
                _xvars_from_v6_column(sub.iloc[0]) if not sub.empty else []
            )
    else:
        x_variables_by_tag = {str(t): [] for t in result_df["Tag"].unique()}

    counts_nc = df_p4["Final_Class"].value_counts().to_dict()
    counts_v5 = df_v5["Final_Class"].value_counts().to_dict()
    counts_v6 = df_v6["Final_Class"].value_counts().to_dict()
    counts_f = result_df["Final_Class"].value_counts().to_dict()

    summary: Dict[str, Any] = {
        "Fusion_Method": (
            "Ordinal severity union across Auto No Causal (Part4) + Outlier detection + Outlier detection (using data model); "
            "missing sub-model row treated as Normal for that engine."
        ),
        "Total_Result_Rows": int(len(result_df)),
        "Total_Tags": int(result_df["Tag"].nunique()),
        "Fused_Abnormal_Rows": int((result_df["Final_Status"] == "Abnormal").sum()),
        "Fused_Normal_Rows": int((result_df["Final_Status"] == "Normal").sum()),
        "Threshold_Mode": (
            "Testing V7 — tri-engine fusion (process + controls + multivariate view)"
        ),
    }
    for k, v in counts_f.items():
        summary[f"Fused_Count_{k.replace(' ', '_')}"] = int(v)
    for k, v in counts_nc.items():
        summary[f"Auto_no_causal_count_{str(k).replace(' ', '_')}"] = int(v)
    for k, v in counts_v5.items():
        summary[f"Outlier_detection_count_{str(k).replace(' ', '_')}"] = int(v)
    for k, v in counts_v6.items():
        summary[f"Outlier_detection_data_model_count_{str(k).replace(' ', '_')}"] = int(v)

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
                "Reason": (
                    _build_reason(
                        r.get("Final_Class"),
                        r.get("Direction"),
                        r.get("Limit_Crossed"),
                    )
                    + " | "
                    + str(r.get("Fusion_Rationale") or "")
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
                            "Reason": (
                                _build_reason(
                                    r.get("Final_Class"),
                                    r.get("Direction"),
                                    r.get("Limit_Crossed"),
                                )
                                + " | "
                                + str(r.get("Fusion_Rationale") or "")
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
