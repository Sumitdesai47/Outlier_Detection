"""Flask routes: anomaly detection (part2) and outlier detection (part3)."""
from __future__ import annotations

import json
import uuid

from flask import Blueprint, Response, jsonify, render_template, request

from services.anomaly_pipeline import compute_top10_roots_with_paths, run_drift_phase_from_uploads
from services.drift_detection_service import build_plot_figure_for_tag, run_drift_detection_on_xlsx
from services.json_sanitize import jsonable
from services.part2_plots import build_part2_target_plot_json
from services.session_cache import part2_load, part2_store, part3_load, part3_store
from services.uploads import save_upload_to_temp

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    tab = (request.args.get("tab") or "part2").strip().lower()
    if tab not in {"part2", "part3"}:
        tab = "part2"
    return render_template("index.html", active_tab=tab)


@bp.route("/part2/drift-causes", methods=["POST"])
def part2_drift_causes():
    causal_model_xlsx = request.files.get("causal_model_xlsx")
    time_series_xlsx = request.files.get("time_series_xlsx")
    if not causal_model_xlsx or not time_series_xlsx:
        return render_template(
            "index.html",
            error="Missing one or both required files.",
            active_tab="part2",
        )

    historic_ratio = float(request.form.get("historic_ratio", "0.70"))
    lookback_months = int(request.form.get("lookback_months", "2"))
    top_k_drift = int(request.form.get("top_k_drift", "10"))

    causal_model_path = save_upload_to_temp(causal_model_xlsx, suffix=".xlsx")
    time_series_path = save_upload_to_temp(time_series_xlsx, suffix=".xlsx")

    try:
        out = run_drift_phase_from_uploads(
            time_series_path,
            causal_model_path,
            historic_ratio=historic_ratio,
            lookback_months=lookback_months,
            top_n_drift_tags=top_k_drift,
        )
    except Exception as e:
        return render_template("index.html", error=str(e), active_tab="part2")

    session_blob = out.pop("session_blob")
    result_id = uuid.uuid4().hex
    part2_store(result_id, session_blob)

    client_cfg = jsonable({"resultId": result_id, "tags": out.get("top_target_tags") or []})

    return render_template(
        "results.html",
        part2={
            "result_id": result_id,
            "client_config_json": json.dumps(client_cfg),
            "summary": out.get("summary") or {},
            "top_drift_rows": out.get("top_drift_rows") or [],
        },
        part3=None,
        active_tab="part2",
    )


@bp.route("/api/part2/plot/<result_id>")
def api_part2_plot(result_id: str):
    tag = (request.args.get("tag") or "").strip()
    if not tag:
        return jsonify({"error": "missing tag"}), 400
    ctx = part2_load(result_id)
    if not ctx:
        return jsonify({"error": "expired or invalid session"}), 404
    drift_raw = (ctx.get("drift_raw_times") or {}).get(tag)
    try:
        fig_json = build_part2_target_plot_json(
            ctx["smoothed_df"],
            ctx["timestamp_col"],
            tag,
            drift_raw,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return Response(fig_json, mimetype="application/json")


@bp.route("/api/part2/roots/<result_id>")
def api_part2_roots(result_id: str):
    tag = (request.args.get("tag") or "").strip()
    if not tag:
        return jsonify({"error": "missing tag"}), 400
    ctx = part2_load(result_id)
    if not ctx:
        return jsonify({"error": "expired or invalid session"}), 404
    try:
        rows = compute_top10_roots_with_paths(ctx, tag)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"rows": jsonable(rows)})


@bp.route("/api/part3/plot/<result_id>")
def api_part3_plot(result_id: str):
    tag = (request.args.get("tag") or "").strip()
    if not tag:
        return jsonify({"error": "missing tag"}), 400
    compare = [c.strip() for c in request.args.getlist("compare") if c and str(c).strip()]
    ctx = part3_load(result_id)
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


@bp.route("/part3/drift-detection", methods=["POST"])
def part3_drift_detection():
    drift_xlsx = request.files.get("drift_xlsx")
    if not drift_xlsx:
        return render_template("index.html", error="Missing file: drift_xlsx", active_tab="part3")

    drift_xlsx_path = save_upload_to_temp(drift_xlsx, suffix=".xlsx")
    result = run_drift_detection_on_xlsx(drift_xlsx_path)

    df_for_script = result.pop("df_for_script")
    out_df = result.pop("out_df")
    result_id = uuid.uuid4().hex
    part3_store(result_id, df_for_script, out_df)
    tag_names = [t["tag"] for t in result["tag_summaries"]]
    all_plot_tags = sorted(c for c in df_for_script.columns if c != "Timestamp")
    monthly_pages = result["monthly_pages_by_tag"]
    months_by_tag_idx = [
        [p["month"] for p in monthly_pages.get(s["tag"], [])] for s in result["tag_summaries"]
    ]

    return render_template(
        "results.html",
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


def register(app):
    app.register_blueprint(bp)
