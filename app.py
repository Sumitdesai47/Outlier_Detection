from __future__ import annotations

import os
import pickle
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
from flask import Flask, Response, jsonify, render_template, request

from services.causal_service import parse_path_tags
from services.causal_service import extract_child_nodes_from_propagation_paths
from services.drift_service import rank_drift_tags
from services.drift_service import compute_drift_metrics, detect_first_drift_time
from services.drift_detection_service import build_plot_figure_for_tag, run_drift_detection_on_xlsx
from services.outlier_service import detect_outliers_in_wide_xlsx
from services.pipeline_wrapper import run_root_cause_pipeline
from services.reason_service import build_top_causes_with_reasons
from services.time_series_utils import format_date_us_mdy, load_wide_time_series_xlsx, safe_parse_datetime_series

APP_ROOT = Path(__file__).resolve().parent

app = Flask(__name__)

# In-memory + temp-file cache for Part 3 plots (survives Flask debug reloader child process).
_PART3_CACHE: dict[str, dict[str, Any]] = {}


def _part3_cache_file(result_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"anomaly_pt3_{result_id}.pkl")


def _part3_store_context(result_id: str, df_for_script: Any, out_df: Any) -> None:
    ctx = {"df_for_script": df_for_script, "out_df": out_df}
    _PART3_CACHE[result_id] = ctx
    try:
        with open(_part3_cache_file(result_id), "wb") as f:
            pickle.dump(ctx, f, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError:
        pass


def _part3_load_context(result_id: str) -> dict[str, Any] | None:
    if result_id in _PART3_CACHE:
        return _PART3_CACHE[result_id]
    path = _part3_cache_file(result_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            ctx = pickle.load(f)
        _PART3_CACHE[result_id] = ctx
        return ctx
    except OSError:
        return None


def _save_upload_to_temp(file_storage, *, suffix: str) -> str:
    tmpdir = tempfile.mkdtemp(prefix="anomaly_upload_")
    path = os.path.join(tmpdir, file_storage.filename)
    # If user didn't name file, still ensure suffix.
    if not file_storage.filename:
        path = os.path.join(tmpdir, f"upload{suffix}")
    file_storage.save(path)
    return path


@app.route("/")
def index():
    tab = (request.args.get("tab") or "part1").strip().lower()
    if tab not in {"part1", "part2", "part3"}:
        tab = "part1"
    return render_template("index.html", active_tab=tab)


@app.route("/part1/outliers", methods=["POST"])
def part1_outliers():
    anomaly_xlsx = request.files.get("anomaly_xlsx")
    if not anomaly_xlsx:
        return render_template("index.html", error="Missing file: anomaly_xlsx")

    robust_z_threshold = float(request.form.get("robust_z_threshold", "3.5"))
    plot_tag = request.form.get("plot_tag") or None

    file_path = _save_upload_to_temp(anomaly_xlsx, suffix=".xlsx")

    out = detect_outliers_in_wide_xlsx(
        file_path,
        robust_z_threshold=robust_z_threshold,
        plot_tag=plot_tag,
        timestamp_base_datetime=None,
        timestamp_unit="D",
    )

    flags_df = out["flags_df"].copy()
    flags_df["timestamp"] = flags_df["timestamp"].astype(str)

    status_df = out["outlier_status_df"].copy()
    if "first_outlier_timestamp" in status_df.columns:
        status_df["first_outlier_timestamp"] = status_df["first_outlier_timestamp"].astype(str)
        status_df.loc[status_df["num_flags"] == 0, "first_outlier_timestamp"] = "NA"
        status_df.loc[status_df["first_outlier_timestamp"].isin(["NaT", "nan", "None"]), "first_outlier_timestamp"] = "NA"

    # Small UI table: top tags by number of flags (helps performance when many tags exist).
    status_max_rows = 30
    status_truncated = len(status_df) > status_max_rows
    status_rows_df = status_df.head(status_max_rows)

    # Limit rows for rendering.
    flags_max_rows = 500
    truncated = len(flags_df) > flags_max_rows
    flags_rows_df = flags_df.head(flags_max_rows)

    return render_template(
        "results.html",
        part1={
            "robust_z_threshold": out["robust_z_threshold"],
            "flags_count": int(len(flags_df)),
            "flags_truncated": truncated,
            "flags_max_rows": flags_max_rows,
            "flags_rows": flags_rows_df.to_dict(orient="records"),
            "plot_tag_used": out["plot_tag_used"],
            "plot_html": out["plot_html"],
            "plot_tag_is_outlier": out["plot_tag_is_outlier"],
            "plot_tag_first_outlier_timestamp": str(out["plot_tag_first_outlier_timestamp"]),
            "plot_tag_num_flags": out["plot_tag_num_flags"],
            "status_rows": status_rows_df.to_dict(orient="records"),
            "status_truncated": status_truncated,
            "status_max_rows": status_max_rows,
        },
        part2=None,
        part3=None,
        active_tab="part1",
    )


@app.route("/part2/drift-causes", methods=["POST"])
def part2_drift_causes():
    causal_model_xlsx = request.files.get("causal_model_xlsx")
    time_series_xlsx = request.files.get("time_series_xlsx")
    if not causal_model_xlsx or not time_series_xlsx:
        return render_template("index.html", error="Missing one or both required files.")

    target_col = (request.form.get("target_col") or "C2_Splitter_DP").strip()
    historic_ratio = float(request.form.get("historic_ratio", "0.70"))
    top_k_drift = int(request.form.get("top_k_drift", "10"))
    show_plots = request.form.get("show_plots") == "1"

    causal_model_path = _save_upload_to_temp(causal_model_xlsx, suffix=".xlsx")
    time_series_path = _save_upload_to_temp(time_series_xlsx, suffix=".xlsx")

    # 1) Drift ranking -> top drift tags
    drift_out = rank_drift_tags(
        time_series_path,
        target_col=target_col,
        historic_ratio=historic_ratio,
        top_k=top_k_drift,
        sheet_name=0,
    )
    time_series_df = drift_out["df"]
    top_tags_df = drift_out["top_tags_df"].copy()

    top_drift_tags = []
    for _, r in top_tags_df.iterrows():
        top_drift_tags.append(
            {
                **r.to_dict(),
                "X_Drift_Time": format_date_us_mdy(r.get("X_Drift_Time")) if not pd.isna(r.get("X_Drift_Time")) else "NA",
                "p_value": r.get("p_value", r.get("p_value", None)) if "p_value" in r else r.get("p_value", None),
            }
        )

    selected_drift_tags = top_tags_df["X_Tag"].astype(str).tolist()

    # 2) Child node extraction from propagation paths
    children_out = extract_child_nodes_from_propagation_paths(
        causal_model_path,
        drift_tags=selected_drift_tags,
        sheet_name="Chain_Matrix_Exhaustive",
        allowed_tags=set(time_series_df.columns),
    )
    children_set = sorted(children_out["children_set"])
    children_by_drift = children_out.get("children_by_drift", {})

    # 3) Build example paths for Pipeline.py:
    #    Pipeline uses `parse_path_tags` and checks if target_col and X_Tag appear in the path.
    #    We'll include propagation paths that contain both `target_col` and at least one child cause.
    example_paths: List[str] = []
    if children_set:
        causal_df = pd.read_excel(causal_model_path, sheet_name="Chain_Matrix_Exhaustive")
        propagation_col = None
        for c in causal_df.columns:
            lc = str(c).lower()
            if "propagation" in lc and "path" in lc:
                propagation_col = c
                break
        if propagation_col is None:
            for c in causal_df.columns:
                if "propagation" in str(c).lower():
                    propagation_col = c
                    break
        if propagation_col is None:
            propagation_col = causal_df.columns[0]

        raw_paths = (
            causal_df[propagation_col]
            .dropna()
            .astype(str)
            .map(str.strip)
            .loc[lambda s: s != ""]
            .unique()
            .tolist()
        )

        child_set = set(children_set)
        for p in raw_paths:
            tags = parse_path_tags(p)
            if target_col not in tags:
                continue
            if any(ch in tags for ch in child_set):
                example_paths.append(p)
        example_paths = list(dict.fromkeys(example_paths))[:2000]

    # 4) Run Pipeline.py scoring -> top causes
    top_causes = []
    if children_set:
        pipeline_out = run_root_cause_pipeline(
            time_series_df=time_series_df,
            target_col=target_col,
            candidate_causes=children_set,
            example_paths=example_paths,
            historic_ratio=historic_ratio,
            top_n_root_causes=10,
        )
        results = pipeline_out["results"]
        all_scores_df = results["all_scores_df"]
        top_root_df = results["top_root_df"]
        top_causes = build_top_causes_with_reasons(all_scores_df, top_root_df, top_n=10)

        if show_plots and len(top_causes) > 0:
            # Precompute Plotly timeline HTML per cause so the UI can open it on demand.
            split_index = int(len(time_series_df) * historic_ratio)
            split_index = max(1, min(split_index, len(time_series_df) - 1))
            historic = time_series_df.iloc[:split_index]
            current = time_series_df.iloc[split_index:]

            target_drift_time = drift_out.get("target_drift_time", None)
            target_drift_time = (
                None
                if pd.isna(target_drift_time)
                else safe_parse_datetime_series(pd.Series([target_drift_time])).iloc[0]
            )

            for cause in top_causes:
                tag = str(cause.get("cause_tag", "")).strip()
                if not tag or tag not in time_series_df.columns:
                    cause["plot_html"] = ""
                    continue

                # Compute drift time (first significant deviation) for vertical line on the plot.
                _ = compute_drift_metrics(historic[tag], current[tag])
                cause_drift_time = detect_first_drift_time(
                    time_series_df, tag, split_index, "Timestamp"
                )
                cause_drift_time = (
                    None
                    if pd.isna(cause_drift_time)
                    else safe_parse_datetime_series(pd.Series([cause_drift_time])).iloc[0]
                )

                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(
                        x=time_series_df["Timestamp"],
                        y=time_series_df[tag],
                        mode="lines",
                        name=tag,
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        x=time_series_df["Timestamp"],
                        y=time_series_df[target_col],
                        mode="lines",
                        name=target_col,
                        opacity=0.55,
                    )
                )

                if cause_drift_time is not None and not pd.isna(cause_drift_time):
                    fig.add_vline(
                        x=cause_drift_time,
                        line_width=2,
                        line_dash="dash",
                        line_color="red",
                    )
                if target_drift_time is not None and not pd.isna(target_drift_time):
                    fig.add_vline(
                        x=target_drift_time,
                        line_width=2,
                        line_dash="dot",
                        line_color="orange",
                    )

                fig.update_layout(
                    title=f"Cause timeline: {tag}",
                    xaxis_title="Timestamp",
                    yaxis_title="Value",
                    template="plotly_white",
                    height=460,
                    legend=dict(orientation="h"),
                    margin=dict(l=40, r=20, t=60, b=40),
                )
                cause["plot_html"] = fig.to_html(full_html=False, include_plotlyjs=False)

    # Convert children_by_drift mapping for UI clarity.
    children_set_for_ui = children_set

    return render_template(
        "results.html",
        part1=None,
        part3=None,
        part2={
            "top_drift_tags": top_drift_tags,
            "children_set": children_set_for_ui,
            "children_by_drift": children_by_drift,
            "top_causes": top_causes,
            "plot_html": None,
        },
        active_tab="part2",
    )


@app.route("/api/part3/plot/<result_id>")
def api_part3_plot(result_id: str):
    tag = (request.args.get("tag") or "").strip()
    if not tag:
        return jsonify({"error": "missing tag"}), 400
    compare = [c.strip() for c in request.args.getlist("compare") if c and str(c).strip()]
    ctx = _part3_load_context(result_id)
    if not ctx:
        return jsonify({"error": "expired or invalid session"}), 404
    try:
        fig = build_plot_figure_for_tag(
            ctx["df_for_script"],
            ctx["out_df"],
            tag,
            compare_tags=compare,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return Response(fig.to_json(), mimetype="application/json")


@app.route("/part3/drift-detection", methods=["POST"])
def part3_drift_detection():
    drift_xlsx = request.files.get("drift_xlsx")
    if not drift_xlsx:
        return render_template("index.html", error="Missing file: drift_xlsx", active_tab="part3")

    drift_xlsx_path = _save_upload_to_temp(drift_xlsx, suffix=".xlsx")
    result = run_drift_detection_on_xlsx(drift_xlsx_path)

    df_for_script = result.pop("df_for_script")
    out_df = result.pop("out_df")
    result_id = uuid.uuid4().hex
    _part3_store_context(result_id, df_for_script, out_df)
    tag_names = [t["tag"] for t in result["tag_summaries"]]
    all_plot_tags = sorted(c for c in df_for_script.columns if c != "Timestamp")
    monthly_pages = result["monthly_pages_by_tag"]
    months_by_tag_idx = [
        [p["month"] for p in monthly_pages.get(s["tag"], [])] for s in result["tag_summaries"]
    ]

    return render_template(
        "results.html",
        part1=None,
        part2=None,
        part3={
            "result_id": result_id,
            "tag_names": tag_names,
            "all_plot_tags": all_plot_tags,
            "months_by_tag_idx": months_by_tag_idx,
            "tag_summaries": result["tag_summaries"],
            "details_by_tag": result["details_by_tag"],
            "monthly_pages_by_tag": result["monthly_pages_by_tag"],
        },
        active_tab="part3",
    )


if __name__ == "__main__":
    # Development server (for production, run behind a WSGI server).
    app.run(host="127.0.0.1", port=5000, debug=True)

