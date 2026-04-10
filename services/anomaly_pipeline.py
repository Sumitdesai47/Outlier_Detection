"""
Anomaly detection: drift ranking (fast pass) + per-tag root-cause analysis (on demand).

Initial upload only runs drift + data prep. Root-cause / XGBoost runs when the user picks a tag.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from .causal_service import _find_propagation_path_column
from .detail_pipeline_prep import (
    graph_from_propagation_paths,
    load_detail_pipeline_module,
    prepare_smoothed_from_wide_df,
)
from .time_series_utils import load_wide_time_series_xlsx


def run_drift_phase_from_prepared_wide(
    df: pd.DataFrame,
    causal_propagation_paths: List[str],
    *,
    historic_ratio: float = 0.70,
    lookback_months: int = 2,
    top_n_drift_tags: int = 10,
    rolling_window: str = "5D",
    rolling_min_periods: int = 1,
    timestamp_col: str = "Timestamp",
) -> Dict[str, Any]:
    """
    Drift ranking from an in-memory wide frame and list of propagation path strings
    (same semantics as Chain_Matrix_Exhaustive). Root-cause loop is skipped.
    """
    dp = load_detail_pipeline_module()
    use_cols = [c for c in df.columns if c not in {"Timestamp_raw"}]
    work = df[use_cols].copy()
    graph = graph_from_propagation_paths(causal_propagation_paths)

    _raw_df, smoothed_df, numeric_cols, start_date, end_date = prepare_smoothed_from_wide_df(
        work,
        timestamp_col=timestamp_col,
        lookback_months=lookback_months,
        rolling_window=rolling_window,
        rolling_min_periods=rolling_min_periods,
    )

    graph_nodes_in_data = [n for n in graph["nodes"] if n in smoothed_df.columns]
    numeric_cols = [c for c in numeric_cols if c in graph_nodes_in_data]
    if not numeric_cols:
        raise ValueError(
            "No overlapping tag columns between time-series data and causal graph nodes. "
            "Check tag names match propagation paths."
        )

    config: Dict[str, Any] = dict(dp.CONFIG)
    config["timestamp_col"] = timestamp_col
    config["historic_ratio"] = historic_ratio
    config["top_n_drift_tags"] = top_n_drift_tags
    config["lookback_months"] = lookback_months
    config["rolling_window"] = rolling_window
    config["rolling_min_periods"] = rolling_min_periods

    drift_df = dp.calculate_drift_for_all_tags(
        smoothed_df=smoothed_df[[timestamp_col] + numeric_cols].copy(),
        numeric_cols=numeric_cols,
        timestamp_col=timestamp_col,
        historic_ratio=historic_ratio,
    )

    top_drift_df = drift_df.head(top_n_drift_tags).copy()
    top_targets = top_drift_df["Tag"].astype(str).tolist()

    drift_display_minimal = []
    for _, row in top_drift_df.iterrows():
        drift_display_minimal.append(
            {
                "Tag": row["Tag"],
                "Drift_Score": None if pd.isna(row.get("Drift_Score")) else float(row["Drift_Score"]),
            }
        )

    summary_row = {
        "Data_Start_Date_Used": str(start_date),
        "Data_End_Date_Used": str(end_date),
        "Lookback_Months": lookback_months,
        "Smoothing": rolling_window,
        "Historic_Ratio": historic_ratio,
        "Total_Numeric_Tags_In_Window": len(numeric_cols),
        "Top_Drift_Targets_Listed": len(top_targets),
        "Causal_Sheet": graph["sheet_used"],
    }

    drift_raw_times = {str(r["Tag"]): r["Drift_Start_Time"] for _, r in top_drift_df.iterrows()}
    summary_row["Drift_Raw_Times"] = {k: str(v) for k, v in drift_raw_times.items() if v is not None}

    session_blob: Dict[str, Any] = {
        "smoothed_df": smoothed_df,
        "timestamp_col": timestamp_col,
        "drift_raw_times": drift_raw_times,
        "graph": graph,
        "numeric_cols": numeric_cols,
        "config": config,
        "top_target_tags": top_targets,
    }

    return {
        "top_drift_rows": drift_display_minimal,
        "top_target_tags": top_targets,
        "summary": summary_row,
        "session_blob": session_blob,
    }


def run_drift_phase_from_uploads(
    time_series_xlsx_path: str,
    causal_xlsx_path: str,
    *,
    historic_ratio: float = 0.70,
    lookback_months: int = 2,
    top_n_drift_tags: int = 10,
    rolling_window: str = "5D",
    rolling_min_periods: int = 1,
    timestamp_col: str = "Timestamp",
) -> Dict[str, Any]:
    """
    Expensive root-cause loop is skipped. Returns minimal drift table + session blob for lazy analysis.
    """
    wide = load_wide_time_series_xlsx(time_series_xlsx_path, timestamp_col_name=timestamp_col)
    use_cols = [c for c in wide.columns if c not in {"Timestamp_raw"}]
    df = wide[use_cols].copy()

    cm = pd.read_excel(causal_xlsx_path, sheet_name="Chain_Matrix_Exhaustive")
    if cm.empty:
        raise ValueError("Chain_Matrix_Exhaustive sheet is empty.")
    path_col = _find_propagation_path_column(cm)
    paths = (
        cm[path_col]
        .dropna()
        .astype(str)
        .map(str.strip)
        .loc[lambda s: s != ""]
        .unique()
        .tolist()
    )
    return run_drift_phase_from_prepared_wide(
        df,
        paths,
        historic_ratio=historic_ratio,
        lookback_months=lookback_months,
        top_n_drift_tags=top_n_drift_tags,
        rolling_window=rolling_window,
        rolling_min_periods=rolling_min_periods,
        timestamp_col=timestamp_col,
    )


def compute_top10_roots_with_paths(session_blob: Dict[str, Any], target_tag: str) -> List[Dict[str, Any]]:
    """Run Detail_Pipeline root-cause model for one target; top 10 rows with propagation path."""
    allowed = [str(t) for t in session_blob.get("top_target_tags") or []]
    if str(target_tag) not in allowed:
        raise ValueError("Selected tag is not in the current top-drift list. Re-run analysis.")

    dp = load_detail_pipeline_module()
    graph = session_blob["graph"]
    smoothed_df = session_blob["smoothed_df"]
    timestamp_col = session_blob["timestamp_col"]
    numeric_cols = session_blob["numeric_cols"]
    config = session_blob["config"]
    target_y = str(target_tag)

    rel = dp.get_target_relations(target_y, graph)
    candidate_causes = rel["direct_causes"] + rel["indirect_causes"]
    if len(candidate_causes) == 0:
        candidate_causes = [c for c in numeric_cols if c != target_y]

    target_work_cols = [timestamp_col, target_y] + [c for c in candidate_causes if c in smoothed_df.columns]
    target_work_df = smoothed_df[target_work_cols].copy().dropna(subset=[target_y]).reset_index(drop=True)

    _scores_df, top_root_df, _model_summary = dp.run_root_cause_for_target(
        work_df=target_work_df,
        target_col=target_y,
        candidate_causes=candidate_causes,
        graph=graph,
        config=config,
    )

    if top_root_df.empty:
        return []

    n = int(config.get("top_n_root_causes_per_target", 10))
    rows_out: List[Dict[str, Any]] = []
    for _, r in top_root_df.head(n).iterrows():
        x_tag = str(r["X_Tag"])
        path_str = ""
        if "One_Path" in r.index and pd.notna(r["One_Path"]) and str(r["One_Path"]).strip():
            path_str = str(r["One_Path"]).strip()
        else:
            paths = dp.find_paths_to_target(
                source=x_tag,
                target=target_y,
                children_map=graph["children"],
                max_depth=config["max_path_depth"],
            )
            path_str = " -> ".join(paths[0]) if paths else f"{x_tag} -> {target_y}"

        score = r["Final_Root_Cause_Score"] if "Final_Root_Cause_Score" in r.index else None
        score_f = None if score is None or pd.isna(score) else float(score)

        rows_out.append(
            {
                "root_cause": x_tag,
                "root_cause_score": score_f,
                "propagation_path": path_str,
            }
        )

    return rows_out


def run_target_root_cause_from_uploads(
    time_series_xlsx_path: str,
    causal_xlsx_path: str,
    *,
    target_tag: str,
    end_date_str: str | None = None,
    historic_ratio: float = 0.70,
    lookback_months: int = 2,
    rolling_window: str = "5D",
    rolling_min_periods: int = 1,
    timestamp_col: str = "Timestamp",
) -> Dict[str, Any]:
    """
    Dummy-tab workflow:
    1) Build causes list for requested target from causal graph relations.
    2) Run anomaly root-cause scoring for that target and return top-10 roots.
    """
    dp = load_detail_pipeline_module()
    wide = load_wide_time_series_xlsx(time_series_xlsx_path, timestamp_col_name=timestamp_col)
    use_cols = [c for c in wide.columns if c not in {"Timestamp_raw"}]
    df = wide[use_cols].copy()
    if timestamp_col not in df.columns:
        raise ValueError(f"Missing timestamp column: {timestamp_col}")
    df[timestamp_col] = pd.to_datetime(df[timestamp_col], errors="coerce")
    df = df.dropna(subset=[timestamp_col]).copy()
    if end_date_str:
        end_day = pd.to_datetime(end_date_str, errors="coerce")
        if pd.isna(end_day):
            raise ValueError("Invalid selected date. Use YYYY-MM-DD.")
        # Date input is day-based; include full selected day.
        cutoff = end_day + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        df = df[df[timestamp_col] <= cutoff].copy()
        if df.empty:
            raise ValueError("No time-series rows found on or before selected date.")

    cm = pd.read_excel(causal_xlsx_path, sheet_name="Chain_Matrix_Exhaustive")
    if cm.empty:
        raise ValueError("Chain_Matrix_Exhaustive sheet is empty.")
    path_col = _find_propagation_path_column(cm)
    paths = (
        cm[path_col]
        .dropna()
        .astype(str)
        .map(str.strip)
        .loc[lambda s: s != ""]
        .unique()
        .tolist()
    )
    graph = graph_from_propagation_paths(paths)

    _raw_df, smoothed_df, numeric_cols, start_date, end_date = prepare_smoothed_from_wide_df(
        df,
        timestamp_col=timestamp_col,
        lookback_months=lookback_months,
        rolling_window=rolling_window,
        rolling_min_periods=rolling_min_periods,
    )
    graph_nodes_in_data = [n for n in graph["nodes"] if n in smoothed_df.columns]
    numeric_cols = [c for c in numeric_cols if c in graph_nodes_in_data]
    if not numeric_cols:
        raise ValueError(
            "No overlapping tag columns between time-series data and causal graph nodes. "
            "Check tag names match propagation paths."
        )

    target = str(target_tag or "").strip()
    if not target:
        raise ValueError("Target tag is required.")
    if target not in smoothed_df.columns:
        raise ValueError(f"Target tag '{target}' not found in time-series columns.")

    config: Dict[str, Any] = dict(dp.CONFIG)
    config["timestamp_col"] = timestamp_col
    config["historic_ratio"] = historic_ratio
    config["lookback_months"] = lookback_months
    config["rolling_window"] = rolling_window
    config["rolling_min_periods"] = rolling_min_periods

    session_blob: Dict[str, Any] = {
        "smoothed_df": smoothed_df,
        "timestamp_col": timestamp_col,
        "drift_raw_times": {},
        "graph": graph,
        "numeric_cols": numeric_cols,
        "config": config,
        "top_target_tags": [target],
    }

    rel = dp.get_target_relations(target, graph)
    causes_list = {
        "target_tag": target,
        "direct_causes": [str(x) for x in rel.get("direct_causes", [])],
        "indirect_causes": [str(x) for x in rel.get("indirect_causes", [])],
        "other_ancestors": [str(x) for x in rel.get("other_ancestors", [])],
    }

    roots = compute_top10_roots_with_paths(session_blob, target)
    return {
        "target_tag": target,
        "causes_list": causes_list,
        "roots_top10": roots,
        "smoothed_df": smoothed_df,
        "timestamp_col": timestamp_col,
        "summary": {
            "Data_Start_Date_Used": str(start_date),
            "Data_End_Date_Used": str(end_date),
            "Selected_Last_Date": str(end_date_str or ""),
            "Lookback_Months": lookback_months,
            "Historic_Ratio": historic_ratio,
            "Total_Numeric_Tags_In_Window": len(numeric_cols),
        },
    }
