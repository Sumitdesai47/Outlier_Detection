"""REST APIs for Plant Analysis configurations and result dashboard."""
from __future__ import annotations

import io
import json
import math
from typing import Any, Dict, List

import pandas as pd
from flask import Blueprint, jsonify, request, send_file

from services.plant_analysis_live_cache import build_cache_from_bundle
from services.plant_analysis_live_dashboard import (
    build_live_dashboard_overview,
    build_live_tag_detail,
)
from services.plant_analysis_live_mysql_sync import sync_live_upload_to_mysql
from services.plant_analysis_live_outlier_runner import run_plant_analysis_live_outlier
from services.plant_analysis_multimodel_runner import (
    run_plant_analysis_multimodel,
    save_upload_for_multimodel,
)
from services.plotly_json_utils import plotly_figure_to_client_json
from services.plant_analysis_results_store import (
    STATUS_BOTH,
    STATUS_OUTLIER,
    STATUS_PROCESS,
    build_run_day_meta,
    build_summary,
    build_tag_context,
    delete_live_cache_for_run,
    get_connection,
    get_run,
    init_db,
    list_filter_options,
    list_runs,
    observation_days_from_points,
    query_points,
    query_points_for_tab,
    save_configuration,
    save_run_with_points,
)

bp = Blueprint("plant_analysis_api", __name__, url_prefix="/plant-analysis/api")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    # Plotly / numpy scalars and arrays from figure JSON
    mod = type(value).__module__
    if mod and mod.startswith("numpy"):
        if hasattr(value, "tolist"):
            return json_safe(value.tolist())
        if hasattr(value, "item"):
            try:
                return json_safe(value.item())
            except (ValueError, TypeError):
                pass
    return value


@bp.route("/health")
def health():
    init_db()
    return jsonify({"ok": True})


@bp.route("/runs", methods=["GET"])
def api_list_runs():
    init_db()
    plant = request.args.get("plant")
    subsystem = request.args.get("subsystem")
    dataset = request.args.get("dataset")
    engine = request.args.get("engine")
    runs = list_runs(engine=engine)
    if plant:
        runs = [r for r in runs if r["plant_name"] == plant]
    if subsystem:
        runs = [r for r in runs if r["subsystem"] == subsystem]
    if dataset:
        runs = [r for r in runs if r["dataset_name"] == dataset]
    return jsonify({"runs": json_safe(runs)})


@bp.route("/filters", methods=["GET"])
def api_filters():
    init_db()
    return jsonify(json_safe(list_filter_options()))


def _tag_summaries_from_points(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    per_tag: Dict[str, Dict[str, int]] = {}
    for point in points:
        tag = str(point.get("tag_name") or "").strip()
        if not tag:
            continue
        row = per_tag.setdefault(
            tag,
            {
                "tag_name": tag,
                "total_points": 0,
                "outlier_only": 0,
                "process_issue_only": 0,
                "both": 0,
                "normal": 0,
            },
        )
        row["total_points"] += 1
        status = str(point.get("status") or "")
        if status == STATUS_OUTLIER:
            row["outlier_only"] += 1
        elif status == STATUS_PROCESS:
            row["process_issue_only"] += 1
        elif status == STATUS_BOTH:
            row["both"] += 1
        else:
            row["normal"] += 1

    summaries: List[Dict[str, Any]] = []
    for row in per_tag.values():
        outlier = row["outlier_only"]
        process = row["process_issue_only"]
        both = row["both"]
        summaries.append(
            {
                "tag_name": row["tag_name"],
                "total_points": row["total_points"],
                "outlier": outlier,
                "process": process,
                "both": outlier + process + both,
                "normal": row["normal"],
                "dual_classified": both,
                "outlier_only": outlier,
                "process_issue_only": process,
            }
        )
    summaries.sort(key=lambda item: str(item["tag_name"]))
    return summaries


def _build_summary_payload(
    *,
    plant_name: str,
    subsystem: str,
    dataset_name: str,
    total_tags: int,
    total_records: int,
    analysis_duration: str,
    points: List[Dict[str, Any]],
    engine: str,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    outlier_exclusive = sum(1 for p in points if p.get("status") == STATUS_OUTLIER)
    process_exclusive = sum(1 for p in points if p.get("status") == STATUS_PROCESS)
    dual_classified = sum(1 for p in points if p.get("status") == STATUS_BOTH)
    observation_days = observation_days_from_points(points)

    payload = {
        "plant_name": plant_name,
        "subsystem": subsystem,
        "dataset_name": dataset_name,
        "total_tags_analyzed": total_tags,
        "total_records_processed": total_records,
        "total_outlier_points": outlier_exclusive,
        "total_process_issue_points": process_exclusive,
        "total_abnormal_points": outlier_exclusive + process_exclusive + dual_classified,
        "analysis_duration": analysis_duration,
        "engine": engine,
        "multimodel_meta": meta if engine == "multimodel_outlier" else None,
        "live_meta": meta if engine == "live_outlier" else None,
        "normal_rows": meta.get("normal_rows"),
        "methodology": meta.get("methodology"),
        "cooling_period_rows": meta.get("cooling_period_rows"),
        "analyzed_timestamps": meta.get("analyzed_timestamps"),
        "observation_days": observation_days,
        "tag_summaries": _tag_summaries_from_points(points),
        "dataset_tags": meta.get("dataset_tags") or sorted({str(p.get("tag_name")) for p in points if p.get("tag_name")}),
        "x_variables_by_tag": meta.get("x_variables_by_tag") or {},
    }
    return json_safe(payload)


@bp.route("/analyze", methods=["POST"])
def api_analyze():
    init_db()
    try:
        plant_name = (request.form.get("plant_name") or "").strip()
        subsystem = (request.form.get("subsystem") or "").strip()
        dataset_name = (request.form.get("dataset_name") or "").strip()
        config = json.loads(request.form.get("config_json") or "{}")
        upload = request.files.get("file")

        if not plant_name or not subsystem:
            return jsonify({"error": "plant_name and subsystem are required"}), 400
        if upload is None or not upload.filename:
            return jsonify({"error": "file is required"}), 400
        if not dataset_name:
            dataset_name = upload.filename

        engine = str(
            request.form.get("engine")
            or config.get("engine")
            or "multimodel_outlier"
        ).strip().lower()
        if engine not in {"multimodel_outlier", "live_outlier"}:
            return jsonify({"error": f"Unsupported engine: {engine}"}), 400

        config_id = save_configuration(
            plant_name=plant_name,
            subsystem=subsystem,
            dataset_name=dataset_name,
            config=config,
        )

        file_path = save_upload_for_multimodel(upload)
        rolling_enabled = config.get("rolling") in (True, "true", "True", 1, "1")
        if rolling_enabled:
            config = {**config, "rolling": True, "duration": "full"}
        if engine == "live_outlier":
            workflow, points, meta = run_plant_analysis_live_outlier(file_path, config)
            mysql_dataset_id = sync_live_upload_to_mysql(
                plant_name=plant_name,
                subsystem=subsystem,
                dataset_name=dataset_name,
                file_path=file_path,
                original_filename=upload.filename or "upload.xlsx",
                bundle=workflow,
            )
            if mysql_dataset_id is not None:
                meta = {**meta, "mysql_dataset_id": mysql_dataset_id}
        else:
            workflow, points, meta = run_plant_analysis_multimodel(file_path, config)

        duration_label = str(config.get("duration") or "Full uploaded dataset")
        total_tags = int(meta.get("total_tags") or 0)
        total_records = int(meta.get("total_records") or 0)
        summary_payload = _build_summary_payload(
            plant_name=plant_name,
            subsystem=subsystem,
            dataset_name=dataset_name,
            total_tags=total_tags,
            total_records=total_records,
            analysis_duration=duration_label,
            points=points,
            engine=engine,
            meta=meta,
        )

        run_id = save_run_with_points(
            configuration_id=config_id,
            plant_name=plant_name,
            subsystem=subsystem,
            dataset_name=dataset_name,
            total_tags=total_tags,
            total_records=total_records,
            analysis_duration=duration_label,
            summary=summary_payload,
            points=points,
        )

        if engine == "live_outlier":
            build_cache_from_bundle(run_id, workflow)

        return jsonify(
            {
                "ok": True,
                "run_id": run_id,
                "configuration_id": config_id,
                "engine": engine,
                "summary": json_safe(build_summary(run_id)),
                "day_meta": json_safe(build_run_day_meta(run_id)),
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Analysis failed: {exc}"}), 500


@bp.route("/results/summary", methods=["GET"])
def api_results_summary():
    init_db()
    run_id = request.args.get("run_id")
    if not run_id:
        runs = list_runs()
        if not runs:
            return jsonify({"error": "No analysis runs found"}), 404
        run_id = runs[0]["id"]
    summary = build_summary(run_id)
    if not summary:
        return jsonify({"error": "Run not found"}), 404
    return jsonify(json_safe(summary))


@bp.route("/results/days", methods=["GET"])
def api_results_days():
    init_db()
    run_id = request.args.get("run_id")
    if not run_id:
        return jsonify({"error": "run_id is required"}), 400
    return jsonify(json_safe(build_run_day_meta(run_id)))


@bp.route("/results/points", methods=["GET"])
def api_results_points():
    init_db()
    run_id = request.args.get("run_id")
    tab = request.args.get("tab", "summary")
    tag = request.args.get("tag") or None
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    severity = request.args.get("severity") or None

    if not run_id:
        runs = list_runs()
        if not runs:
            return jsonify({"points": [], "tags": []})
        run_id = runs[0]["id"]

    points = query_points_for_tab(
        run_id=run_id,
        tab=tab,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
        severity=severity,
    )
    tags = sorted({p["tag_name"] for p in points})
    return jsonify({"run_id": run_id, "points": json_safe(points), "tags": tags})


@bp.route("/results/series", methods=["GET"])
def api_results_series():
    init_db()
    run_id = request.args.get("run_id")
    tag = request.args.get("tag") or None
    compare = [str(v).strip() for v in request.args.getlist("compare") if str(v).strip()]
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    if not run_id:
        runs = list_runs()
        if not runs:
            return jsonify({"points": []})
        run_id = runs[0]["id"]
    points = query_points(
        run_id=run_id,
        tag=tag,
        tags=compare or None,
        date_from=date_from,
        date_to=date_to,
    )
    return jsonify({"run_id": run_id, "points": json_safe(points)})


@bp.route("/results/tag-context", methods=["GET"])
def api_results_tag_context():
    init_db()
    run_id = (request.args.get("run_id") or "").strip()
    tag = (request.args.get("tag") or "").strip()
    if not run_id or not tag:
        return jsonify({"error": "run_id and tag are required"}), 400
    return jsonify(json_safe(build_tag_context(run_id, tag)))


@bp.route("/results/live", methods=["GET"])
def api_results_live():
    init_db()
    run_id = (request.args.get("run_id") or "").strip()
    day = (request.args.get("day") or "").strip() or None
    if not run_id:
        runs = list_runs(engine="live_outlier")
        if not runs:
            return jsonify({"error": "No live outlier runs found"}), 404
        run_id = runs[0]["id"]
    overview = build_live_dashboard_overview(run_id, day=day)
    return jsonify(json_safe(overview))


@bp.route("/results/live/detail", methods=["GET"])
def api_results_live_detail():
    init_db()
    run_id = (request.args.get("run_id") or "").strip()
    day = (request.args.get("day") or "").strip()
    tag = (request.args.get("tag") or "").strip()
    compare = [str(v).strip() for v in request.args.getlist("compare") if str(v).strip()]
    if not run_id:
        return jsonify({"error": "run_id is required"}), 400
    if not day:
        return jsonify({"error": "day is required"}), 400
    if not tag:
        return jsonify({"error": "tag is required"}), 400
    try:
        detail = build_live_tag_detail(run_id, day=day, tag=tag, compare_tags=compare)
        return jsonify(json_safe(detail))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.route("/results/plot", methods=["GET"])
def api_results_plot():
    init_db()
    run_id = (request.args.get("run_id") or "").strip()
    tag = (request.args.get("tag") or "").strip()
    tab = (request.args.get("tab") or "").strip() or None
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None

    if not run_id or not tag:
        return jsonify({"error": "run_id and tag are required"}), 400

    points = query_points(
        run_id=run_id,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    if not points:
        return jsonify({"error": "No points available for tag"}), 404
    fig = build_plant_analysis_tag_plot(points, tag, tab=tab)
    return jsonify(json_safe(plotly_figure_to_client_json(fig)))


@bp.route("/results/download", methods=["GET"])
def api_results_download():
    init_db()
    run_id = request.args.get("run_id")
    tab = request.args.get("tab", "summary")
    fmt = (request.args.get("format") or "csv").lower()
    tag = request.args.get("tag") or None

    if not run_id:
        runs = list_runs()
        if not runs:
            return jsonify({"error": "No runs available"}), 404
        run_id = runs[0]["id"]

    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404

    if tab == "summary":
        rows = build_summary(run_id).get("tag_summaries", [])
        filename = f"plant_analysis_summary_{run_id[:8]}"
    else:
        rows = query_points_for_tab(run_id=run_id, tab=tab, tag=tag)
        filename = f"plant_analysis_{tab}_{run_id[:8]}"

    if not rows:
        return jsonify({"error": "No data to export"}), 404

    df = pd.DataFrame(rows)
    if fmt == "xlsx":
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        buf.seek(0)
        return send_file(
            buf,
            as_attachment=True,
            download_name=f"{filename}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    if fmt == "pdf":
        html = _summary_pdf_html(run, rows, tab)
        return send_file(
            io.BytesIO(html.encode("utf-8")),
            as_attachment=True,
            download_name=f"{filename}.html",
            mimetype="text/html",
        )

    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        as_attachment=True,
        download_name=f"{filename}.csv",
        mimetype="text/csv",
    )


@bp.route("/runs/<run_id>", methods=["DELETE"])
def api_delete_run(run_id: str):
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM plant_analysis_result_point WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM plant_analysis_run WHERE id = ?", (run_id,))
        conn.commit()
    delete_live_cache_for_run(run_id)
    return jsonify({"ok": True})


@bp.route("/plants/<plant_name>", methods=["DELETE"])
def api_delete_plant_runs(plant_name: str):
    init_db()
    with get_connection() as conn:
        run_ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM plant_analysis_run WHERE plant_name = ?",
                (plant_name,),
            ).fetchall()
        ]
        conn.execute("DELETE FROM plant_analysis_result_point WHERE run_id IN (SELECT id FROM plant_analysis_run WHERE plant_name = ?)", (plant_name,))
        conn.execute("DELETE FROM plant_analysis_run WHERE plant_name = ?", (plant_name,))
        conn.commit()
    for run_id in run_ids:
        delete_live_cache_for_run(str(run_id))
    return jsonify({"ok": True, "deleted_runs": len(run_ids)})


@bp.route("/areas/<plant_name>/<subsystem>", methods=["DELETE"])
def api_delete_area_runs(plant_name: str, subsystem: str):
    init_db()
    with get_connection() as conn:
        run_ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM plant_analysis_run WHERE plant_name = ? AND subsystem = ?",
                (plant_name, subsystem),
            ).fetchall()
        ]
        conn.execute(
            "DELETE FROM plant_analysis_result_point WHERE run_id IN (SELECT id FROM plant_analysis_run WHERE plant_name = ? AND subsystem = ?)",
            (plant_name, subsystem),
        )
        conn.execute(
            "DELETE FROM plant_analysis_run WHERE plant_name = ? AND subsystem = ?",
            (plant_name, subsystem),
        )
        conn.commit()
    for run_id in run_ids:
        delete_live_cache_for_run(str(run_id))
    return jsonify({"ok": True, "deleted_runs": len(run_ids)})


def _summary_pdf_html(run: Dict[str, Any], rows: List[Dict[str, Any]], tab: str) -> str:
    rows_html = "".join(
        f"<tr>{''.join(f'<td>{row.get(k, '')}</td>' for k in row.keys())}</tr>"
        for row in rows[:200]
    )
    headers = "".join(f"<th>{k}</th>" for k in (rows[0].keys() if rows else []))
    return f"""
    <html><head><title>Plant Analysis Report</title>
    <style>body{{font-family:Segoe UI,sans-serif;padding:24px}}table{{border-collapse:collapse;width:100%}}
    th,td{{border:1px solid #ccc;padding:8px;text-align:left;font-size:12px}}</style></head>
    <body>
    <h1>Plant Analysis Report</h1>
    <p><b>Plant:</b> {run.get('plant_name')} &nbsp; <b>Subsystem:</b> {run.get('subsystem')}</p>
    <p><b>Dataset:</b> {run.get('dataset_name')} &nbsp; <b>Tab:</b> {tab}</p>
    <p><b>Processed:</b> {run.get('processed_at')}</p>
    <table><thead><tr>{headers}</tr></thead><tbody>{rows_html}</tbody></table>
    </body></html>
    """


def register(app):
    app.register_blueprint(bp)
