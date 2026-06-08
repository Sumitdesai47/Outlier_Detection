"""REST APIs for Plant Analysis configurations and result dashboard."""
from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from flask import Blueprint, jsonify, request, send_file

from services.plant_analysis_multimodel_runner import (
    run_plant_analysis_multimodel,
    save_upload_for_multimodel,
)
from services.plant_analysis_results_store import (
    build_summary,
    get_run,
    init_db,
    list_filter_options,
    list_runs,
    query_points,
    save_configuration,
    save_run_with_points,
    tab_status,
)

bp = Blueprint("plant_analysis_api", __name__, url_prefix="/plant-analysis/api")


def _parse_upload_file(file_storage) -> pd.DataFrame:
    filename = (file_storage.filename or "").lower()
    raw = file_storage.read()
    if filename.endswith(".csv"):
        return pd.read_csv(io.BytesIO(raw))
    if filename.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw))
    raise ValueError("Unsupported file type. Upload .xlsx, .xls, or .csv.")


def _detect_columns(df: pd.DataFrame) -> Dict[str, Any]:
    columns = [str(c).strip() for c in df.columns]
    timestamp_column = None
    for col in columns:
        lower = col.lower()
        if any(h in lower for h in ("timestamp", "time", "date", "datetime")):
            timestamp_column = col
            break
    numeric_columns: List[str] = []
    for col in columns:
        if col == timestamp_column:
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        if series.notna().sum() >= max(3, len(df) * 0.5):
            numeric_columns.append(col)
    return {
        "timestamp_column": timestamp_column,
        "tag_columns": numeric_columns,
    }


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
    runs = list_runs()
    if plant:
        runs = [r for r in runs if r["plant_name"] == plant]
    if subsystem:
        runs = [r for r in runs if r["subsystem"] == subsystem]
    if dataset:
        runs = [r for r in runs if r["dataset_name"] == dataset]
    return jsonify({"runs": runs})


@bp.route("/filters", methods=["GET"])
def api_filters():
    init_db()
    return jsonify(list_filter_options())


@bp.route("/analyze", methods=["POST"])
def api_analyze():
    """Save configuration, run multimodel outlier detection, and persist results."""
    init_db()
    try:
        plant_name = (request.form.get("plant_name") or "").strip()
        subsystem = (request.form.get("subsystem") or "").strip()
        dataset_name = (request.form.get("dataset_name") or "").strip()
        config_raw = request.form.get("config_json") or "{}"
        config = json.loads(config_raw)
        upload = request.files.get("file")

        if not plant_name or not subsystem:
            return jsonify({"error": "plant_name and subsystem are required"}), 400
        if upload is None or not upload.filename:
            return jsonify({"error": "file is required"}), 400

        if not dataset_name:
            dataset_name = upload.filename

        config_id = save_configuration(
            plant_name=plant_name,
            subsystem=subsystem,
            dataset_name=dataset_name,
            config=config,
        )

        file_path = save_upload_for_multimodel(upload)
        workflow, points, meta = run_plant_analysis_multimodel(file_path, config)

        duration_label = "Full uploaded dataset"
        total_tags = int(meta.get("total_tags") or 0)
        total_records = int(meta.get("total_records") or 0)

        summary = build_summary_from_points(
            plant_name=plant_name,
            subsystem=subsystem,
            dataset_name=dataset_name,
            total_tags=total_tags,
            total_records=total_records,
            analysis_duration=str(duration_label),
            points=points,
        )
        summary["engine"] = "multimodel_outlier"
        summary["multimodel_meta"] = meta
        summary["normal_rows"] = meta.get("normal_rows")
        summary["tag_summaries"] = _tag_summaries_from_points(points, workflow.get("tag_summaries") or [])

        run_id = save_run_with_points(
            configuration_id=config_id,
            plant_name=plant_name,
            subsystem=subsystem,
            dataset_name=dataset_name,
            total_tags=total_tags,
            total_records=total_records,
            analysis_duration=str(duration_label),
            summary=summary,
            points=points,
        )

        return jsonify(
            {
                "ok": True,
                "run_id": run_id,
                "configuration_id": config_id,
                "summary": build_summary(run_id),
                "engine": "multimodel_outlier",
                "multimodel_meta": meta,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Multimodel analysis failed: {exc}"}), 500


def _tag_summaries_from_points(
    points: List[Dict[str, Any]],
    workflow_summaries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    from services.plant_analysis_results_store import STATUS_BOTH, STATUS_OUTLIER, STATUS_PROCESS

    per_tag: Dict[str, Dict[str, int]] = {}
    for p in points:
        tag = str(p.get("tag_name") or "")
        if not tag:
            continue
        row = per_tag.setdefault(
            tag,
            {"tag_name": tag, "total_points": 0, "outlier_only": 0, "process_issue_only": 0, "both": 0, "normal": 0},
        )
        row["total_points"] += 1
        status = p.get("status")
        if status == STATUS_OUTLIER:
            row["outlier_only"] += 1
        elif status == STATUS_PROCESS:
            row["process_issue_only"] += 1
        elif status == STATUS_BOTH:
            row["both"] += 1
        else:
            row["normal"] += 1

    for item in workflow_summaries:
        tag = str(item.get("tag") or "")
        if not tag or tag in per_tag:
            continue
        per_tag[tag] = {
            "tag_name": tag,
            "total_points": 0,
            "outlier_only": 0,
            "process_issue_only": 0,
            "both": 0,
            "normal": 0,
        }

    summaries = []
    for row in per_tag.values():
        outlier_exclusive = row["outlier_only"]
        process_exclusive = row["process_issue_only"]
        summaries.append(
            {
                "tag_name": row["tag_name"],
                "total_points": row["total_points"],
                "outlier": outlier_exclusive,
                "process": process_exclusive,
                "both": outlier_exclusive + process_exclusive,
                "normal": row["normal"],
                "dual_classified": row["both"],
                "outlier_only": outlier_exclusive,
                "process_issue_only": process_exclusive,
            }
        )
    return summaries


def build_summary_from_points(
    *,
    plant_name: str,
    subsystem: str,
    dataset_name: str,
    total_tags: int,
    total_records: int,
    analysis_duration: str,
    points: List[Dict[str, Any]],
) -> Dict[str, Any]:
    from services.plant_analysis_results_store import (
        STATUS_BOTH,
        STATUS_OUTLIER,
        STATUS_PROCESS,
    )

    outlier_exclusive = sum(1 for p in points if p["status"] == STATUS_OUTLIER)
    process_exclusive = sum(1 for p in points if p["status"] == STATUS_PROCESS)

    return {
        "plant_name": plant_name,
        "subsystem": subsystem,
        "dataset_name": dataset_name,
        "total_tags_analyzed": total_tags,
        "total_records_processed": total_records,
        "total_outlier_points": outlier_exclusive,
        "total_process_issue_points": process_exclusive,
        "total_abnormal_points": outlier_exclusive + process_exclusive,
        "analysis_duration": analysis_duration,
    }


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
    return jsonify(summary)


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

    status = tab_status(tab)
    points = query_points(
        run_id=run_id,
        status=status,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
        severity=severity,
    )
    tags = sorted({p["tag_name"] for p in query_points(run_id=run_id)})
    return jsonify({"run_id": run_id, "points": points, "tags": tags})


@bp.route("/results/series", methods=["GET"])
def api_results_series():
    """Full time series for graphing (all points including Normal)."""
    init_db()
    run_id = request.args.get("run_id")
    tag = request.args.get("tag") or None
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
        date_from=date_from,
        date_to=date_to,
    )
    return jsonify({"run_id": run_id, "points": points})


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
        status = tab_status(tab)
        rows = query_points(run_id=run_id, status=status, tag=tag)
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


def _summary_pdf_html(run: Dict[str, Any], rows: List[Dict[str, Any]], tab: str) -> str:
    summary = run.get("summary") or {}
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
