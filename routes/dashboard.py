"""Flask routes: anomaly detection (part2) and outlier detection (part3)."""
from __future__ import annotations

import json
import logging
import os
import uuid
from io import BytesIO
from datetime import date, datetime

import pandas as pd
import pymysql
import pymysql.err
from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)

from services import db_queries
from services import db_repository as db_repo
from services.db_config import is_configured, ping_mysql
from services.anomaly_pipeline import (
    compute_top10_roots_with_paths,
    run_drift_phase_from_uploads,
    run_target_root_cause_from_uploads,
)
from services.drift_detection_service import build_plot_figure_for_tag, run_drift_detection_on_xlsx
from services.auto_workflow_docs import DOCS as AUTO_WORKFLOW_DOCS
from services.auto_without_causal_outlier_drift import (
    run_auto_identification_outlier_drift,
    run_auto_without_causal_outlier_drift,
    run_testing_deviation_spike_v4_outlier_drift,
    run_testing_deviation_spike_v5_outlier_drift,
    run_testing_fusion_v7_outlier_drift,
    run_testing_top5_corr_regression_outlier_drift,
    run_without_clean_data_outlier_drift,
)
from services.json_sanitize import jsonable
from services.part2_plots import build_part2_target_plot_json
from services.hourly_detail_service import build_scheduled_job_tag_detail
from services.live_outlier_dashboard import (
    build_live_outlier_excel_day_tag_detail,
    build_part8_display_from_stored_analysis,
)
from services.live_outlier_excel_upload import insert_live_outlier_excel_upload
from services.scheduled_anomaly_runner import floor_day_utc_naive
from services.session_cache import part2_load, part2_store, part3_load, part3_store
from services.uploads import save_upload_to_temp
from services.dataset_upload_parse import validate_excel_filename
from services.plant_dataset_upload import insert_plant_upload_transaction
from services.dashboard_overview import build_dashboard_snapshot
from services.live_dashboard_status import build_plant_live_status
from services.scheduled_anomaly_runner import run_live_dashboard_catchup

bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)

# If this path points to an existing XLSX, the app treats a causal matrix as "available"
# and opens the Anomaly detection tab by default (unless ?tab= or last workflow overrides).
_CAUSAL_ENV = "CAUSAL_MATRIX_PATH"


def _causal_matrix_file_configured() -> bool:
    p = (os.environ.get(_CAUSAL_ENV) or "").strip()
    return bool(p) and os.path.isfile(p)


def _resolve_home_tab() -> str:
    q = (request.args.get("tab") or "").strip().lower()
    if q in {"part2", "part3", "part4", "part5", "part6", "part7", "part8", "part9", "part10"}:
        return q
    if q == "db":
        return "part4"
    if _causal_matrix_file_configured():
        return "part4"
    last = session.get("last_workflow")
    if last == "anomaly":
        return "part4"
    if last == "outlier":
        return "part4"
    return "part4"


def _has_causal_matrix_context() -> bool:
    return _causal_matrix_file_configured() or session.get("last_workflow") == "anomaly"


def _render_index(*, active_tab: str, error: str | None = None):
    return render_template(
        "index.html",
        active_tab=active_tab,
        has_causal_matrix_context=_has_causal_matrix_context(),
        causal_matrix_env=_CAUSAL_ENV,
        database_enabled=is_configured(),
        error=error,
    )


def _dashboard_chart_figure_json(labels: list, values: list) -> str:
    """Plotly figure JSON for overview bar chart (completed scheduled jobs per day)."""
    fig = {
        "data": [
            {
                "type": "bar",
                "x": labels,
                "y": values,
                "marker": {"color": "rgba(214, 31, 38, 0.88)"},
            }
        ],
        "layout": {
            "margin": {"l": 44, "r": 12, "t": 20, "b": 72},
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(248,250,252,1)",
            "font": {
                "family": "Segoe UI, system-ui, sans-serif",
                "size": 11,
                "color": "#64748b",
            },
            "xaxis": {"tickangle": -40, "title": {"text": ""}},
            "yaxis": {"title": {"text": "Completed runs"}, "gridcolor": "#e2e8f0"},
            "height": 300,
            "showlegend": False,
        },
    }
    return json.dumps(fig)


@bp.route("/dashboard")
def overview_dashboard():
    """Main overview: cross-module metrics and deep links."""
    database_enabled = is_configured()
    base_kw = dict(
        database_enabled=database_enabled,
        has_causal_matrix_context=_has_causal_matrix_context(),
        causal_matrix_env=_CAUSAL_ENV,
    )
    if not database_enabled:
        return render_template(
            "dashboard.html",
            **base_kw,
            db_reachable=None,
            mysql_diagnostic=None,
            dash=None,
            chart_json=None,
            live_outlier_datasets=[],
        )

    ok, ping_err = ping_mysql()
    if not ok:
        logger.warning("Dashboard ping failed: %s", ping_err)
        return render_template(
            "dashboard.html",
            **base_kw,
            db_reachable=False,
            mysql_diagnostic=ping_err,
            dash=None,
            chart_json=None,
            live_outlier_datasets=[],
        )

    try:
        db_repo.apply_schema_if_needed()
    except pymysql.err.ProgrammingError as e:
        logger.warning("Dashboard schema apply: %s", e)
        return render_template(
            "dashboard.html",
            **base_kw,
            db_reachable=False,
            mysql_diagnostic=f"Schema: {e}",
            dash=None,
            chart_json=None,
            live_outlier_datasets=[],
        )
    except pymysql.err.OperationalError as e:
        logger.warning("Dashboard schema operational: %s", e)
        msg = e.args[1] if len(e.args) > 1 else str(e)
        return render_template(
            "dashboard.html",
            **base_kw,
            db_reachable=False,
            mysql_diagnostic=msg,
            dash=None,
            chart_json=None,
            live_outlier_datasets=[],
        )

    dash = build_dashboard_snapshot()
    chart_json = _dashboard_chart_figure_json(
        dash.get("chart_labels") or [],
        dash.get("chart_values") or [],
    )
    try:
        live_outlier_datasets = db_queries.list_live_outlier_excel_datasets()
    except Exception as e:
        logger.warning("dashboard live outlier list: %s", e)
        live_outlier_datasets = []
    return render_template(
        "dashboard.html",
        **base_kw,
        db_reachable=True,
        mysql_diagnostic=None,
        dash=dash,
        chart_json=chart_json,
        live_outlier_datasets=live_outlier_datasets,
    )


def _parse_page(val: str | None, default: int = 1) -> int:
    try:
        return max(1, int(val))
    except (TypeError, ValueError):
        return default


def _total_pages(total: int, per_page: int) -> int:
    if total <= 0:
        return 1
    return max(1, (total + per_page - 1) // per_page)


def _data_browse_unreachable_response(tab: str):
    return render_template(
        "data_browse.html",
        database_enabled=True,
        db_unreachable=True,
        tab=tab,
        ts_rows=[],
        ts_total=0,
        ts_page=1,
        ts_total_pages=1,
        causal_rows=[],
        causal_total=0,
        causal_page=1,
        causal_total_pages=1,
        ts_data_rows=[],
        ts_data_total=0,
        ts_data_page=1,
        ts_data_total_pages=1,
        causal_data_rows=[],
        causal_data_total=0,
        causal_data_page=1,
        causal_data_total_pages=1,
        all_tables=[],
        browse_table=None,
        browse_columns=[],
        browse_rows=[],
        browse_total=0,
        browse_page=1,
        browse_total_pages=1,
        ts_obs_distinct_dataset_count=0,
        page_size=db_queries.PAGE_SIZE,
    )


@bp.route("/")
def index():
    return _render_index(active_tab=_resolve_home_tab())


def _hourly_results_unreachable(
    mysql_diagnostic: str | None = None, schema_error: str | None = None
):
    return render_template(
        "hourly_results.html",
        database_enabled=True,
        db_unreachable=True,
        mysql_diagnostic=mysql_diagnostic,
        schema_error=schema_error,
        job=None,
        drifts=[],
        parse_error=None,
        selected_day_value="",
        completed_days_iso=[],
        plants=[],
        selected_plant_id=None,
        selected_plant=None,
        plant_needs_mapping=False,
        live_plant_status=None,
        live_latest_scheduled_job=None,
    )


def _sanitize_hourly_drifts_for_json(drifts: list) -> list:
    """Ensure drift rows serialize in Jinja |tojson (e.g. Decimal → float)."""
    out: list = []
    for r in drifts or []:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        s = row.get("drift_score")
        if s is not None:
            try:
                row["drift_score"] = float(s)
            except (TypeError, ValueError):
                row["drift_score"] = None
        out.append(row)
    return out


def _live_dashboard_manual_trigger_authorized() -> bool:
    expected = (os.environ.get("LIVE_DASHBOARD_MANUAL_TOKEN") or "").strip()
    if not expected:
        return True
    h = (request.headers.get("X-Live-Dashboard-Token") or "").strip()
    if h == expected:
        return True
    body = request.get_json(silent=True) or {}
    if str(body.get("token") or "").strip() == expected:
        return True
    q = (request.args.get("token") or "").strip()
    return q == expected


@bp.route("/api/live-dashboard/plant-status")
def api_live_dashboard_plant_status():
    if not is_configured():
        return jsonify({"error": "database_not_configured"}), 503
    pid = request.args.get("plant", type=int)
    if pid is None:
        return jsonify({"error": "missing plant query param"}), 400
    try:
        return jsonify(build_plant_live_status(pid))
    except Exception as e:
        logger.exception("api_live_dashboard_plant_status: %s", e)
        return jsonify({"error": "failed"}), 500


@bp.route("/api/live-dashboard/catchup", methods=["POST"])
def api_live_dashboard_catchup():
    if not is_configured():
        return jsonify({"error": "database_not_configured"}), 503
    if not _live_dashboard_manual_trigger_authorized():
        return jsonify({"error": "unauthorized", "hint": "Set X-Live-Dashboard-Token header"}), 401
    body = request.get_json(silent=True) or {}
    plant_id = None
    if body.get("plant_dataset_id") is not None:
        try:
            plant_id = int(body["plant_dataset_id"])
        except (TypeError, ValueError):
            return jsonify({"error": "invalid plant_dataset_id"}), 400
    max_day_runs = None
    if body.get("max_day_runs") is not None:
        try:
            max_day_runs = int(body["max_day_runs"])
        except (TypeError, ValueError):
            return jsonify({"error": "invalid max_day_runs"}), 400
    try:
        summary = run_live_dashboard_catchup(
            plant_dataset_id=plant_id, max_day_runs=max_day_runs
        )
        # Always 200 when the handler ran; clients use summary["ok"] and summary["message"].
        return jsonify(summary), 200
    except Exception as e:
        logger.exception("api_live_dashboard_catchup: %s", e)
        return jsonify({"ok": False, "message": str(e)}), 500


@bp.route("/hourly-results")
def hourly_results():
    if not is_configured():
        return render_template(
            "hourly_results.html",
            database_enabled=False,
            db_unreachable=False,
            job=None,
            drifts=[],
            parse_error=None,
            selected_day_value="",
            completed_days_iso=[],
            plants=[],
            selected_plant_id=None,
            selected_plant=None,
            plant_needs_mapping=False,
            mysql_diagnostic=None,
            schema_error=None,
            live_plant_status=None,
            live_latest_scheduled_job=None,
        )
    ok, ping_err = ping_mysql()
    if not ok:
        logger.warning("MySQL ping failed (/hourly-results): %s", ping_err)
        return _hourly_results_unreachable(mysql_diagnostic=ping_err)
    try:
        db_repo.apply_schema_if_needed()
    except pymysql.err.ProgrammingError as e:
        logger.warning("MySQL schema apply failed (/hourly-results): %s", e)
        return _hourly_results_unreachable(
            schema_error=str(e),
            mysql_diagnostic=(
                "The database answered, but applying db/schema/*.sql failed. "
                "Often this means a migration was already partially applied or MySQL version mismatch. "
                "Try: python scripts/init_db.py"
            ),
        )
    except pymysql.err.OperationalError as e:
        logger.warning("MySQL operational error during schema (/hourly-results): %s", e)
        msg = e.args[1] if len(e.args) > 1 else str(e)
        return _hourly_results_unreachable(
            mysql_diagnostic=f"During schema update: {msg}",
        )

    parse_error: str | None = None
    day_raw = (request.args.get("day") or "").strip()
    plant_param = request.args.get("plant", type=int)
    job: dict | None = None
    drifts: list = []
    selected_day_value = ""
    plants: list = []
    selected_plant_id: int | None = None
    selected_plant: dict | None = None
    plant_needs_mapping = False
    completed_days_iso: list[str] = []
    live_plant_status: dict | None = None
    live_latest_scheduled_job: dict | None = None

    try:
        plants = db_queries.list_plants_for_dashboard()
    except Exception as e:
        logger.warning("hourly results list plants: %s", e)
        plants = []

    if plants:
        by_id = {int(p["dataset_id"]): p for p in plants}
        if plant_param is not None and plant_param in by_id:
            selected_plant_id = plant_param
        else:
            selected_plant_id = int(plants[0]["dataset_id"])
        selected_plant = by_id.get(selected_plant_id)
        plant_needs_mapping = not (
            selected_plant
            and selected_plant.get("timeseries_dataset_id") is not None
            and selected_plant.get("causal_dataset_id") is not None
        )

        if not plant_needs_mapping and selected_plant_id is not None:
            try:
                completed = db_queries.scheduled_list_completed_days_for_plant(
                    selected_plant_id, 2500
                )
                completed_days_iso = [d.isoformat() for d in completed]
            except Exception as e:
                logger.warning("hourly results list days: %s", e)
                completed_days_iso = []

            if day_raw:
                try:
                    selected_day = date.fromisoformat(day_raw)
                    selected_day_value = selected_day.isoformat()
                    job = db_queries.scheduled_latest_completed_job_for_plant_and_day(
                        selected_plant_id, selected_day
                    )
                    if job:
                        drifts = db_queries.scheduled_drift_rows_for_job(int(job["id"]))
                except ValueError:
                    parse_error = "Invalid day. Use the calendar picker (YYYY-MM-DD)."
                    selected_day_value = day_raw[:10] if len(day_raw) >= 10 else day_raw
            else:
                job = db_queries.scheduled_latest_completed_job_for_plant(selected_plant_id)
                if job and job.get("hour_bucket"):
                    hb = job["hour_bucket"]
                    if hasattr(hb, "date"):
                        selected_day_value = hb.date().isoformat()
                if job:
                    drifts = db_queries.scheduled_drift_rows_for_job(int(job["id"]))

            if selected_plant_id is not None:
                try:
                    live_plant_status = build_plant_live_status(int(selected_plant_id))
                except Exception as e:
                    logger.warning("build_plant_live_status: %s", e)
                if job is None:
                    try:
                        live_latest_scheduled_job = db_queries.scheduled_latest_job_for_plant(
                            int(selected_plant_id)
                        )
                    except Exception as e:
                        logger.warning("scheduled_latest_job_for_plant: %s", e)

    drifts = _sanitize_hourly_drifts_for_json(drifts)

    return render_template(
        "hourly_results.html",
        database_enabled=True,
        db_unreachable=False,
        job=job,
        drifts=drifts,
        parse_error=parse_error,
        selected_day_value=selected_day_value,
        completed_days_iso=completed_days_iso,
        plants=plants,
        selected_plant_id=selected_plant_id,
        selected_plant=selected_plant,
        plant_needs_mapping=plant_needs_mapping,
        mysql_diagnostic=None,
        schema_error=None,
        live_plant_status=live_plant_status,
        live_latest_scheduled_job=live_latest_scheduled_job,
    )


@bp.route("/api/hourly-results/detail")
def api_hourly_results_detail():
    job_id = request.args.get("job_id", type=int)
    tag = (request.args.get("tag") or "").strip()
    compare = [str(c).strip() for c in request.args.getlist("compare") if c and str(c).strip()]
    day_raw = (request.args.get("day") or "").strip()
    if not job_id or not tag:
        return jsonify({"error": "missing job_id or tag"}), 400
    selected_day = None
    if day_raw:
        try:
            selected_day = date.fromisoformat(day_raw)
        except ValueError:
            return jsonify({"error": "invalid day format; expected YYYY-MM-DD"}), 400
    try:
        detail = build_scheduled_job_tag_detail(
            job_id,
            tag,
            selected_day=selected_day,
            compare_tags=compare,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("api_hourly_results_detail failed: %s", e)
        return jsonify({"error": "Failed to build detail"}), 500
    return jsonify(
        {
            "tag": detail["tag"],
            "drift_score": detail.get("drift_score"),
            "roots": jsonable(detail.get("roots") or []),
            "roots_error": detail.get("roots_error"),
            "plot": json.loads(detail["plot_json"]),
        }
    )


@bp.route("/hourly-results/detail")
def hourly_result_detail():
    job_id = request.args.get("job_id", type=int)
    tag = (request.args.get("tag") or "").strip()
    base_kw = dict(
        database_enabled=is_configured(),
        db_unreachable=False,
        error=None,
        job=None,
        plot_json=None,
        roots=None,
        roots_error=None,
        tag="",
        drift_score=None,
        summary=None,
    )
    if not is_configured():
        return render_template("hourly_result_detail.html", **base_kw, database_enabled=False)
    try:
        db_repo.apply_schema_if_needed()
    except pymysql.err.OperationalError as e:
        logger.warning("MySQL unreachable (/hourly-results/detail): %s", e)
        return render_template("hourly_result_detail.html", **base_kw, db_unreachable=True)

    if not job_id or not tag:
        return render_template(
            "hourly_result_detail.html",
            **base_kw,
            error="Missing job_id or tag. Open this page from the Hourly results table.",
        )

    try:
        detail = build_scheduled_job_tag_detail(job_id, tag)
    except ValueError as e:
        return render_template("hourly_result_detail.html", **base_kw, error=str(e))
    except Exception as e:
        logger.exception("hourly_result_detail failed: %s", e)
        return render_template(
            "hourly_result_detail.html",
            **base_kw,
            error="Could not build detail view. Check logs.",
        )

    return render_template(
        "hourly_result_detail.html",
        database_enabled=True,
        db_unreachable=False,
        error=None,
        job=detail["job"],
        plot_json=detail["plot_json"],
        roots=detail["roots"],
        roots_error=detail.get("roots_error"),
        tag=detail["tag"],
        drift_score=detail.get("drift_score"),
        summary=detail.get("summary") or {},
    )


def _live_outlier_results_unreachable(
    mysql_diagnostic: str | None = None, schema_error: str | None = None
):
    return render_template(
        "live_outlier_results.html",
        database_enabled=True,
        db_unreachable=True,
        mysql_diagnostic=mysql_diagnostic,
        schema_error=schema_error,
        job=None,
        drifts=[],
        parse_error=None,
        selected_day_value="",
        completed_days_iso=[],
        plants=[],
        selected_plant_id=None,
        selected_plant=None,
        plant_needs_mapping=False,
        live_plant_status=None,
        live_latest_scheduled_job=None,
        has_outlier_day=False,
        live_outlier_show_catchup=False,
        obs_first_iso=None,
        obs_last_iso=None,
        excel_datasets=[],
        selected_excel_dataset_id=None,
        data_source="excel",
        has_live_outlier_panel=False,
        active_source_label="",
        part8_display=None,
        live_outlier_day_selected=False,
        analysis_error=None,
        no_stored_analysis=False,
    )


@bp.route("/live-outlier-results")
def live_outlier_results():
    if not is_configured():
        return render_template(
            "live_outlier_results.html",
            database_enabled=False,
            db_unreachable=False,
            job=None,
            drifts=[],
            parse_error=None,
            selected_day_value="",
            completed_days_iso=[],
            plants=[],
            selected_plant_id=None,
            selected_plant=None,
            plant_needs_mapping=False,
            mysql_diagnostic=None,
            schema_error=None,
            live_plant_status=None,
            live_latest_scheduled_job=None,
            has_outlier_day=False,
            live_outlier_show_catchup=False,
            obs_first_iso=None,
            obs_last_iso=None,
            excel_datasets=[],
            selected_excel_dataset_id=None,
            data_source="excel",
            has_live_outlier_panel=False,
            active_source_label="",
            part8_display=None,
            live_outlier_day_selected=False,
            analysis_error=None,
            no_stored_analysis=False,
        )
    ok, ping_err = ping_mysql()
    if not ok:
        logger.warning("MySQL ping failed (/live-outlier-results): %s", ping_err)
        return _live_outlier_results_unreachable(mysql_diagnostic=ping_err)
    try:
        db_repo.apply_schema_if_needed()
    except pymysql.err.ProgrammingError as e:
        logger.warning("MySQL schema apply failed (/live-outlier-results): %s", e)
        return _live_outlier_results_unreachable(
            schema_error=str(e),
            mysql_diagnostic=(
                "The database answered, but applying db/schema/*.sql failed. "
                "Often this means a migration was already partially applied or MySQL version mismatch. "
                "Try: python scripts/init_db.py"
            ),
        )
    except pymysql.err.OperationalError as e:
        logger.warning("MySQL operational error during schema (/live-outlier-results): %s", e)
        msg = e.args[1] if len(e.args) > 1 else str(e)
        return _live_outlier_results_unreachable(
            mysql_diagnostic=f"During schema update: {msg}",
        )

    parse_error: str | None = None
    day_raw = (request.args.get("day") or "").strip()
    excel_param = request.args.get("excel_dataset", type=int)
    job: dict | None = None
    drifts: list = []
    selected_day_value = ""
    try:
        plants = db_queries.list_plants_for_dashboard()
    except Exception as e:
        logger.warning("live outlier list plants: %s", e)
        plants = []
    excel_datasets: list = []
    selected_plant_id: int | None = None
    selected_plant: dict | None = None
    selected_excel_dataset_id: int | None = None
    plant_needs_mapping = False
    data_source: str = "excel"
    has_live_outlier_panel = False
    completed_days_iso: list[str] = []
    live_plant_status: dict | None = None
    live_latest_scheduled_job: dict | None = None
    has_outlier_day = False
    live_outlier_show_catchup = False
    obs_first_iso: str | None = None
    obs_last_iso: str | None = None
    part8_display: dict | None = None
    live_outlier_day_selected = False
    analysis_error: str | None = None
    no_stored_analysis = False

    try:
        excel_datasets = db_queries.list_live_outlier_excel_datasets()
    except Exception as e:
        logger.warning("live outlier list excel datasets: %s", e)
        excel_datasets = []

    excel_id_set = {int(r["id"]) for r in excel_datasets}
    if excel_param is not None and excel_param in excel_id_set:
        selected_excel_dataset_id = excel_param
    elif excel_datasets:
        selected_excel_dataset_id = int(excel_datasets[0]["id"])

    selected_day: date | None = None

    if selected_excel_dataset_id is not None:
        has_live_outlier_panel = True
        min_o = None
        max_o = None
        try:
            min_o = db_queries.live_outlier_excel_dataset_min_observed_at(
                int(selected_excel_dataset_id)
            )
            max_o = db_queries.live_outlier_excel_dataset_max_observed_at(
                int(selected_excel_dataset_id)
            )
            if min_o is not None:
                obs_first_iso = floor_day_utc_naive(min_o).date().isoformat()
            if max_o is not None:
                obs_last_iso = floor_day_utc_naive(max_o).date().isoformat()
        except Exception as e:
            logger.warning("live outlier excel obs bounds: %s", e)

        if max_o is not None:
            try:
                obs_days = db_queries.live_outlier_excel_distinct_observation_days(
                    int(selected_excel_dataset_id)
                )
                completed_days_iso = [d.isoformat() for d in obs_days]
            except Exception as e:
                logger.warning("live outlier observation days: %s", e)
                completed_days_iso = []

            if day_raw:
                try:
                    picked = date.fromisoformat(day_raw)
                    selected_day = picked
                    selected_day_value = picked.isoformat()
                    live_outlier_day_selected = True
                except ValueError:
                    parse_error = "Invalid day. Use the calendar picker (YYYY-MM-DD)."
                    selected_day_value = day_raw[:10] if len(day_raw) >= 10 else day_raw
            if selected_day is None:
                try:
                    selected_day = floor_day_utc_naive(max_o).date()
                    selected_day_value = selected_day.isoformat()
                    live_outlier_day_selected = True
                except Exception as e:
                    logger.warning("live outlier latest day: %s", e)

        if selected_day is not None and parse_error is None:
            part8_light, drifts, has_outlier_day, analysis_error = (
                build_part8_display_from_stored_analysis(
                    int(selected_excel_dataset_id), selected_day
                )
            )
            if part8_light:
                part8_display = part8_light
            elif (
                selected_day is not None
                and parse_error is None
                and analysis_error is None
            ):
                no_stored_analysis = True

    drifts = _sanitize_hourly_drifts_for_json(drifts)

    active_source_label = ""
    if selected_excel_dataset_id is not None:
        meta = db_queries.live_outlier_excel_dataset_by_id(int(selected_excel_dataset_id))
        if meta:
            active_source_label = str(meta.get("dataset_name") or "")

    return render_template(
        "live_outlier_results.html",
        database_enabled=True,
        db_unreachable=False,
        job=job,
        drifts=drifts,
        parse_error=parse_error,
        selected_day_value=selected_day_value,
        completed_days_iso=completed_days_iso,
        plants=plants,
        excel_datasets=excel_datasets,
        selected_plant_id=selected_plant_id,
        selected_plant=selected_plant,
        selected_excel_dataset_id=selected_excel_dataset_id,
        data_source=data_source,
        has_live_outlier_panel=has_live_outlier_panel,
        active_source_label=active_source_label,
        plant_needs_mapping=plant_needs_mapping,
        mysql_diagnostic=None,
        schema_error=None,
        live_plant_status=live_plant_status,
        live_latest_scheduled_job=live_latest_scheduled_job,
        has_outlier_day=has_outlier_day,
        live_outlier_show_catchup=live_outlier_show_catchup,
        obs_first_iso=obs_first_iso,
        obs_last_iso=obs_last_iso,
        part8_display=part8_display,
        live_outlier_day_selected=live_outlier_day_selected,
        analysis_error=analysis_error,
        no_stored_analysis=no_stored_analysis,
    )


@bp.route("/api/live-outlier-results/detail")
def api_live_outlier_results_detail():
    excel_id = request.args.get("excel_dataset", type=int)
    tag = (request.args.get("tag") or "").strip()
    compare = [str(c).strip() for c in request.args.getlist("compare") if c and str(c).strip()]
    day_raw = (request.args.get("day") or "").strip()
    if not excel_id or not tag:
        return jsonify({"error": "missing excel_dataset or tag"}), 400
    if day_raw:
        try:
            selected_day = date.fromisoformat(day_raw)
        except ValueError:
            return jsonify({"error": "invalid day format; expected YYYY-MM-DD"}), 400
    else:
        max_o = db_queries.live_outlier_excel_dataset_max_observed_at(int(excel_id))
        if max_o is None:
            return jsonify({"error": "no observations for this dataset"}), 400
        selected_day = floor_day_utc_naive(max_o).date()
    try:
        detail = build_live_outlier_excel_day_tag_detail(
            excel_id,
            selected_day,
            tag,
            selected_day=selected_day,
            compare_tags=compare,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("api_live_outlier_results_detail failed: %s", e)
        return jsonify({"error": "Failed to build detail"}), 500
    return jsonify(
        {
            "tag": detail["tag"],
            "drift_score": detail.get("drift_score"),
            "roots": jsonable(detail.get("roots") or []),
            "roots_error": detail.get("roots_error"),
            "plot": json.loads(detail["plot_json"]),
        }
    )


@bp.route("/live-outlier-results/detail")
def live_outlier_result_detail():
    excel_id = request.args.get("excel_dataset", type=int)
    tag = (request.args.get("tag") or "").strip()
    day_raw = (request.args.get("day") or "").strip()
    base_kw = dict(
        database_enabled=is_configured(),
        db_unreachable=False,
        error=None,
        job=None,
        plot_json=None,
        roots=None,
        roots_error=None,
        tag="",
        drift_score=None,
        summary=None,
        detail_day_iso=None,
        detail_plant_id=None,
        detail_excel_dataset_id=None,
        data_source="excel",
    )
    if not is_configured():
        return render_template(
            "live_outlier_result_detail.html", **base_kw, database_enabled=False
        )
    try:
        db_repo.apply_schema_if_needed()
    except pymysql.err.OperationalError as e:
        logger.warning("MySQL unreachable (/live-outlier-results/detail): %s", e)
        return render_template("live_outlier_result_detail.html", **base_kw, db_unreachable=True)

    if not tag or not day_raw or not excel_id:
        return render_template(
            "live_outlier_result_detail.html",
            **base_kw,
            error="Missing excel_dataset, tag, or day. Open this page from Live Outlier detection.",
        )
    try:
        selected_day = date.fromisoformat(day_raw)
    except ValueError:
        return render_template(
            "live_outlier_result_detail.html",
            **base_kw,
            error="Invalid day format.",
        )

    try:
        detail = build_live_outlier_excel_day_tag_detail(
            excel_id, selected_day, tag, selected_day=selected_day
        )
        data_source = "excel"
    except ValueError as e:
        return render_template("live_outlier_result_detail.html", **base_kw, error=str(e))
    except Exception as e:
        logger.exception("live_outlier_result_detail failed: %s", e)
        return render_template(
            "live_outlier_result_detail.html",
            **base_kw,
            error="Could not build detail view. Check logs.",
        )

    return render_template(
        "live_outlier_result_detail.html",
        database_enabled=True,
        db_unreachable=False,
        error=None,
        job=detail["job"],
        plot_json=detail["plot_json"],
        roots=detail["roots"],
        roots_error=detail.get("roots_error"),
        tag=detail["tag"],
        drift_score=detail.get("drift_score"),
        summary=detail.get("summary") or {},
        detail_day_iso=day_raw.strip(),
        detail_plant_id=None,
        detail_excel_dataset_id=excel_id,
        data_source=data_source,
    )


@bp.route("/data")
def data_browse():
    tab = (request.args.get("tab") or "ts").strip().lower()
    if tab not in ("ts", "causal", "ts_rows", "causal_rows", "tables"):
        tab = "ts"
    ts_page = _parse_page(request.args.get("ts_page"))
    causal_page = _parse_page(request.args.get("causal_page"))
    ts_rows_page = _parse_page(request.args.get("ts_rows_page"))
    causal_rows_page = _parse_page(request.args.get("causal_rows_page"))
    browse_page_req = _parse_page(request.args.get("browse_page"))
    if not is_configured():
        return render_template(
            "data_browse.html",
            database_enabled=False,
            db_unreachable=False,
            tab=tab,
            ts_rows=[],
            ts_total=0,
            ts_page=1,
            ts_total_pages=1,
            causal_rows=[],
            causal_total=0,
            causal_page=1,
            causal_total_pages=1,
            ts_data_rows=[],
            ts_data_total=0,
            ts_data_page=1,
            ts_data_total_pages=1,
            causal_data_rows=[],
            causal_data_total=0,
            causal_data_page=1,
            causal_data_total_pages=1,
            all_tables=[],
            browse_table=None,
            browse_columns=[],
            browse_rows=[],
            browse_total=0,
            browse_page=1,
            browse_total_pages=1,
            ts_obs_distinct_dataset_count=0,
            page_size=db_queries.PAGE_SIZE,
        )
    try:
        db_repo.apply_schema_if_needed()
        ts_list, ts_total, ts_page = db_queries.list_timeseries_datasets_page(ts_page)
        causal_list, causal_total, causal_page = db_queries.list_causal_datasets_page(causal_page)
        ts_data_rows, ts_data_total, ts_rows_page = db_queries.list_timeseries_observations_global_page(
            ts_rows_page
        )
        causal_data_rows, causal_data_total, causal_rows_page = db_queries.list_causal_rows_global_page(
            causal_rows_page
        )
        ts_obs_distinct_dataset_count = db_queries.count_distinct_timeseries_observation_dataset_ids()

        all_tables = db_queries.list_base_tables_for_browse()
        browse_table = None
        browse_columns: list = []
        browse_rows: list = []
        browse_total = 0
        browse_page = 1
        browse_total_pages = 1
        if all_tables:
            names = {t["name"] for t in all_tables}
            want = (request.args.get("browse_table") or "").strip()
            if want in names:
                browse_table = want
            else:
                browse_table = all_tables[0]["name"]
            browse_columns, browse_rows, browse_total, browse_page = db_queries.browse_table_rows_page(
                browse_table, browse_page_req
            )
            browse_total_pages = _total_pages(browse_total, db_queries.PAGE_SIZE)
    except pymysql.err.OperationalError as e:
        logger.warning("MySQL unreachable (/data): %s", e)
        return _data_browse_unreachable_response(tab)
    return render_template(
        "data_browse.html",
        database_enabled=True,
        db_unreachable=False,
        tab=tab,
        ts_rows=ts_list,
        ts_total=ts_total,
        ts_page=ts_page,
        ts_total_pages=_total_pages(ts_total, db_queries.PAGE_SIZE),
        causal_rows=causal_list,
        causal_total=causal_total,
        causal_page=causal_page,
        causal_total_pages=_total_pages(causal_total, db_queries.PAGE_SIZE),
        ts_data_rows=ts_data_rows,
        ts_data_total=ts_data_total,
        ts_data_page=ts_rows_page,
        ts_data_total_pages=_total_pages(ts_data_total, db_queries.PAGE_SIZE),
        causal_data_rows=causal_data_rows,
        causal_data_total=causal_data_total,
        causal_data_page=causal_rows_page,
        causal_data_total_pages=_total_pages(causal_data_total, db_queries.PAGE_SIZE),
        all_tables=all_tables,
        browse_table=browse_table,
        browse_columns=browse_columns,
        browse_rows=browse_rows,
        browse_total=browse_total,
        browse_page=browse_page,
        browse_total_pages=browse_total_pages,
        ts_obs_distinct_dataset_count=ts_obs_distinct_dataset_count,
        page_size=db_queries.PAGE_SIZE,
    )


@bp.route("/data/timeseries/<int:dataset_id>")
def data_timeseries_detail(dataset_id: int):
    if not is_configured():
        abort(404)
    try:
        db_repo.apply_schema_if_needed()
        meta = db_queries.get_timeseries_dataset_meta(dataset_id)
        if not meta:
            abort(404)
        page = _parse_page(request.args.get("page"))
        obs, total, page = db_queries.list_timeseries_observations_page(dataset_id, page)
    except pymysql.err.OperationalError as e:
        logger.warning("MySQL unreachable (timeseries detail): %s", e)
        return render_template(
            "db_connection_error.html",
            database_enabled=is_configured(),
            message="Could not connect to MySQL (connection refused or host unreachable).",
        )
    return render_template(
        "data_timeseries_detail.html",
        database_enabled=True,
        meta=meta,
        rows=obs,
        total=total,
        page=page,
        total_pages=_total_pages(total, db_queries.PAGE_SIZE),
        page_size=db_queries.PAGE_SIZE,
        dataset_id=dataset_id,
    )


@bp.route("/data/causal/<int:dataset_id>")
def data_causal_detail(dataset_id: int):
    if not is_configured():
        abort(404)
    try:
        db_repo.apply_schema_if_needed()
        meta = db_queries.get_causal_dataset_meta(dataset_id)
        if not meta:
            abort(404)
        page = _parse_page(request.args.get("page"))
        rows, total, page = db_queries.list_causal_rows_page(dataset_id, page)
    except pymysql.err.OperationalError as e:
        logger.warning("MySQL unreachable (causal detail): %s", e)
        return render_template(
            "db_connection_error.html",
            database_enabled=is_configured(),
            message="Could not connect to MySQL (connection refused or host unreachable).",
        )
    return render_template(
        "data_causal_detail.html",
        database_enabled=True,
        meta=meta,
        rows=rows,
        total=total,
        page=page,
        total_pages=_total_pages(total, db_queries.PAGE_SIZE),
        page_size=db_queries.PAGE_SIZE,
        dataset_id=dataset_id,
    )


def _dataset_upload_form_error(message: str):
    return render_template(
        "dataset_upload.html",
        database_enabled=is_configured(),
        db_unreachable=False,
        message=message,
        message_kind="danger",
    )


@bp.route("/dataset-upload", methods=["GET", "POST"])
def dataset_upload_page():
    """
    HTML form: plant name + two .xlsx files. Persists to plant_dataset, time_series_data, causal_data.

    JSON API (same fields, multipart): POST /api/dataset-upload

    Example (curl):
      curl -s -X POST http://127.0.0.1:5000/api/dataset-upload \\
        -F plant_name="Plant A" \\
        -F time_series_xlsx=@/path/to/ts.xlsx \\
        -F causal_matrix_xlsx=@/path/to/causal.xlsx

    Example success response (200):
      {"success": true, "dataset_id": 3, "message": "Dataset saved for plant 'Plant A' ...", "error_code": null}

    Example validation error (400):
      {"success": false, "dataset_id": null, "message": "Only Excel .xlsx files are allowed.", "error_code": "validation"}

    Example duplicate plant name (409):
      {"success": false, "dataset_id": null, "message": "A plant with name 'Plant A' already exists...", "error_code": "duplicate_plant"}
    """
    if not is_configured():
        return render_template(
            "dataset_upload.html",
            database_enabled=False,
            db_unreachable=False,
            message=None,
            message_kind=None,
        )
    try:
        db_repo.apply_schema_if_needed()
    except pymysql.err.OperationalError as e:
        logger.warning("MySQL unreachable (/dataset-upload): %s", e)
        return render_template(
            "dataset_upload.html",
            database_enabled=True,
            db_unreachable=True,
            message=None,
            message_kind=None,
        )

    if request.method == "GET":
        return render_template(
            "dataset_upload.html",
            database_enabled=True,
            db_unreachable=False,
            message=None,
            message_kind=None,
        )

    plant_name = (request.form.get("plant_name") or "").strip()
    ts_file = request.files.get("time_series_xlsx")
    causal_file = request.files.get("causal_matrix_xlsx")

    if not ts_file or not getattr(ts_file, "filename", None):
        return _dataset_upload_form_error("Time series Excel file is required.")
    if not causal_file or not getattr(causal_file, "filename", None):
        return _dataset_upload_form_error("Causal matrix Excel file is required.")

    try:
        validate_excel_filename(ts_file.filename)
        validate_excel_filename(causal_file.filename)
    except ValueError as e:
        return _dataset_upload_form_error(str(e))

    try:
        ts_bytes = ts_file.read()
        causal_bytes = causal_file.read()
    except Exception as e:
        return _dataset_upload_form_error(f"Could not read uploaded files: {e}")

    result = insert_plant_upload_transaction(
        plant_name,
        ts_bytes,
        causal_bytes,
        causal_file.filename or "causal.xlsx",
        ts_file.filename or "timeseries.xlsx",
    )
    if not result["success"]:
        kind = "warning" if result.get("error_code") == "duplicate_plant" else "danger"
        return render_template(
            "dataset_upload.html",
            database_enabled=True,
            db_unreachable=False,
            message=result["message"],
            message_kind=kind,
        )

    return render_template(
        "dataset_upload.html",
        database_enabled=True,
        db_unreachable=False,
        message=result["message"],
        message_kind="success",
    )


def _api_status_for_upload(result: dict) -> int:
    code = result.get("error_code")
    if result.get("success"):
        return 200
    if code == "duplicate_plant":
        return 409
    if code in ("validation", "no_database"):
        return 400
    if code == "integrity":
        return 409
    return 500


@bp.route("/api/dataset-upload", methods=["POST"])
def api_dataset_upload():
    """Multipart JSON API; field names match the HTML form (see dataset_upload_page docstring)."""
    if not is_configured():
        return jsonify(
            {
                "success": False,
                "dataset_id": None,
                "message": "Database is not configured (set DATABASE_URL).",
                "error_code": "no_database",
            }
        ), 503

    try:
        db_repo.apply_schema_if_needed()
    except pymysql.err.OperationalError as e:
        logger.warning("MySQL unreachable (/api/dataset-upload): %s", e)
        return jsonify(
            {
                "success": False,
                "dataset_id": None,
                "message": "Cannot connect to MySQL.",
                "error_code": "db_unreachable",
            }
        ), 503

    plant_name = (request.form.get("plant_name") or "").strip()
    ts_file = request.files.get("time_series_xlsx")
    causal_file = request.files.get("causal_matrix_xlsx")

    if not ts_file or not getattr(ts_file, "filename", None):
        return jsonify(
            {
                "success": False,
                "dataset_id": None,
                "message": "Missing file field time_series_xlsx.",
                "error_code": "validation",
            }
        ), 400
    if not causal_file or not getattr(causal_file, "filename", None):
        return jsonify(
            {
                "success": False,
                "dataset_id": None,
                "message": "Missing file field causal_matrix_xlsx.",
                "error_code": "validation",
            }
        ), 400

    try:
        validate_excel_filename(ts_file.filename)
        validate_excel_filename(causal_file.filename)
    except ValueError as e:
        return jsonify(
            {
                "success": False,
                "dataset_id": None,
                "message": str(e),
                "error_code": "validation",
            }
        ), 400

    try:
        ts_bytes = ts_file.read()
        causal_bytes = causal_file.read()
    except Exception as e:
        return jsonify(
            {
                "success": False,
                "dataset_id": None,
                "message": f"Could not read uploads: {e}",
                "error_code": "validation",
            }
        ), 400

    result = insert_plant_upload_transaction(
        plant_name,
        ts_bytes,
        causal_bytes,
        causal_file.filename or "causal.xlsx",
        ts_file.filename or "timeseries.xlsx",
    )
    status = _api_status_for_upload(result)
    body = {
        "success": bool(result.get("success")),
        "dataset_id": result.get("dataset_id"),
        "message": result.get("message"),
        "error_code": result.get("error_code"),
    }
    return jsonify(body), status


def _outlier_excel_upload_form_error(message: str):
    return render_template(
        "outlier_excel_upload.html",
        database_enabled=is_configured(),
        db_unreachable=False,
        message=message,
        message_kind="danger",
    )


@bp.route("/outlier-excel-upload", methods=["GET", "POST"])
def outlier_excel_upload_page():
    """Dataset name + wide time-series .xlsx → ``live_outlier_excel_dataset`` + observations."""
    if not is_configured():
        return render_template(
            "outlier_excel_upload.html",
            database_enabled=False,
            db_unreachable=False,
            message=None,
            message_kind=None,
        )
    try:
        db_repo.apply_schema_if_needed()
    except pymysql.err.OperationalError as e:
        logger.warning("MySQL unreachable (/outlier-excel-upload): %s", e)
        return render_template(
            "outlier_excel_upload.html",
            database_enabled=True,
            db_unreachable=True,
            message=None,
            message_kind=None,
        )

    if request.method == "GET":
        return render_template(
            "outlier_excel_upload.html",
            database_enabled=True,
            db_unreachable=False,
            message=None,
            message_kind=None,
        )

    dataset_name = (request.form.get("dataset_name") or "").strip()
    ts_file = request.files.get("time_series_xlsx")
    if not ts_file or not getattr(ts_file, "filename", None):
        return _outlier_excel_upload_form_error("Excel file is required.")

    try:
        validate_excel_filename(ts_file.filename)
    except ValueError as e:
        return _outlier_excel_upload_form_error(str(e))

    try:
        ts_bytes = ts_file.read()
    except Exception as e:
        return _outlier_excel_upload_form_error(f"Could not read uploaded file: {e}")

    result = insert_live_outlier_excel_upload(
        dataset_name,
        ts_bytes,
        ts_file.filename or "timeseries.xlsx",
    )
    if not result["success"]:
        return render_template(
            "outlier_excel_upload.html",
            database_enabled=True,
            db_unreachable=False,
            message=result["message"],
            message_kind="danger",
        )

    return render_template(
        "outlier_excel_upload.html",
        database_enabled=True,
        db_unreachable=False,
        message=result["message"],
        message_kind="success",
    )


@bp.route("/api/outlier-excel-upload", methods=["POST"])
def api_outlier_excel_upload():
    if not is_configured():
        return jsonify(
            {
                "success": False,
                "dataset_id": None,
                "message": "Database is not configured (set DATABASE_URL).",
                "error_code": "no_database",
            }
        ), 503
    try:
        db_repo.apply_schema_if_needed()
    except pymysql.err.OperationalError as e:
        logger.warning("MySQL unreachable (/api/outlier-excel-upload): %s", e)
        return jsonify(
            {
                "success": False,
                "dataset_id": None,
                "message": "Cannot connect to MySQL.",
                "error_code": "db_unreachable",
            }
        ), 503

    dataset_name = (request.form.get("dataset_name") or "").strip()
    ts_file = request.files.get("time_series_xlsx")
    if not ts_file or not getattr(ts_file, "filename", None):
        return jsonify(
            {
                "success": False,
                "dataset_id": None,
                "message": "Missing file field time_series_xlsx.",
                "error_code": "validation",
            }
        ), 400
    try:
        validate_excel_filename(ts_file.filename)
    except ValueError as e:
        return jsonify(
            {
                "success": False,
                "dataset_id": None,
                "message": str(e),
                "error_code": "validation",
            }
        ), 400
    try:
        ts_bytes = ts_file.read()
    except Exception as e:
        return jsonify(
            {
                "success": False,
                "dataset_id": None,
                "message": f"Could not read upload: {e}",
                "error_code": "validation",
            }
        ), 400

    result = insert_live_outlier_excel_upload(
        dataset_name,
        ts_bytes,
        ts_file.filename or "timeseries.xlsx",
    )
    code = result.get("error_code")
    status = 200 if result.get("success") else (400 if code in ("validation", "no_database") else 500)
    return (
        jsonify(
            {
                "success": bool(result.get("success")),
                "dataset_id": result.get("dataset_id"),
                "message": result.get("message"),
                "error_code": result.get("error_code"),
            }
        ),
        status,
    )


@bp.route("/part2/drift-causes", methods=["POST"])
def part2_drift_causes():
    causal_model_xlsx = request.files.get("causal_model_xlsx")
    time_series_xlsx = request.files.get("time_series_xlsx")
    if not causal_model_xlsx or not time_series_xlsx:
        return _render_index(active_tab="part2", error="Missing one or both required files.")

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
        return _render_index(active_tab="part2", error=str(e))

    session_blob = out.pop("session_blob")
    result_id = uuid.uuid4().hex
    part2_store(result_id, session_blob)

    try:
        ts_id = db_repo.persist_timeseries_xlsx(
            time_series_path, time_series_xlsx.filename or "timeseries.xlsx"
        )
        causal_id = db_repo.persist_causal_xlsx(
            causal_model_path, causal_model_xlsx.filename or "causal.xlsx"
        )
        plant_id = db_queries.find_plant_dataset_id_for_links(ts_id, causal_id)
        db_repo.persist_anomaly_run(
            result_session_uuid=result_id,
            timeseries_dataset_id=ts_id,
            causal_dataset_id=causal_id,
            plant_dataset_id=plant_id,
            historic_ratio=historic_ratio,
            lookback_months=lookback_months,
            top_k_drift=top_k_drift,
            summary=out.get("summary") or {},
            top_drift_rows=out.get("top_drift_rows") or [],
        )
    except pymysql.Error as e:
        logger.exception("MySQL persist failed (Anomaly upload); run continues without DB: %s", e)

    client_cfg = jsonable({"resultId": result_id, "tags": out.get("top_target_tags") or []})

    session["last_workflow"] = "anomaly"
    session.modified = True

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
        database_enabled=is_configured(),
    )


@bp.route("/part2/dummy-root-cause", methods=["POST"])
def part2_dummy_root_cause():
    causal_model_xlsx = request.files.get("dummy_causal_model_xlsx")
    time_series_xlsx = request.files.get("dummy_time_series_xlsx")
    target_tag = (request.form.get("dummy_target_tag") or "").strip()
    end_date_str = (request.form.get("dummy_end_date") or "").strip()
    if not causal_model_xlsx or not time_series_xlsx or not target_tag:
        return _render_index(
            active_tab="part2",
            error="Dummy analysis requires causal XLSX, time-series XLSX, and target tag.",
        )

    historic_ratio = float(request.form.get("dummy_historic_ratio", "0.70"))
    lookback_months = int(request.form.get("dummy_lookback_months", "2"))

    causal_model_path = save_upload_to_temp(causal_model_xlsx, suffix=".xlsx")
    time_series_path = save_upload_to_temp(time_series_xlsx, suffix=".xlsx")
    try:
        out = run_target_root_cause_from_uploads(
            time_series_path,
            causal_model_path,
            target_tag=target_tag,
            end_date_str=end_date_str or None,
            historic_ratio=historic_ratio,
            lookback_months=lookback_months,
        )
        plot_json = build_part2_target_plot_json(
            out["smoothed_df"],
            out["timestamp_col"],
            out["target_tag"],
            drift_time_raw=None,
        )
    except Exception as e:
        return _render_index(active_tab="part2", error=str(e))

    return render_template(
        "dummy_results.html",
        database_enabled=is_configured(),
        target_tag=out["target_tag"],
        causes_list=out["causes_list"],
        roots_top10=out["roots_top10"],
        plot_json=plot_json,
        summary=out["summary"],
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
    try:
        db_repo.persist_anomaly_roots(
            result_session_uuid=result_id, target_tag=tag, rows=jsonable(rows)
        )
    except pymysql.Error as e:
        logger.exception("MySQL persist failed (roots API): %s", e)
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


@bp.route("/api/part4/plot/<result_id>")
def api_part4_plot(result_id: str):
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
        return _render_index(active_tab="part3", error="Missing file: drift_xlsx")

    drift_xlsx_path = save_upload_to_temp(drift_xlsx, suffix=".xlsx")
    result = run_drift_detection_on_xlsx(drift_xlsx_path)

    df_for_script = result.pop("df_for_script")
    out_df = result.pop("out_df")
    result_id = uuid.uuid4().hex
    export_payload = {
        "tag_summaries": result.get("tag_summaries") or [],
        "details_by_tag": result.get("details_by_tag") or {},
        "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
    }
    part3_store(result_id, df_for_script, out_df, export_payload=export_payload)

    try:
        ts_id = db_repo.persist_timeseries_xlsx(
            drift_xlsx_path, drift_xlsx.filename or "outlier.xlsx"
        )
        plant_id = db_queries.find_plant_dataset_id_for_links(ts_id, None)
        db_repo.persist_outlier_run(
            result_session_uuid=result_id,
            timeseries_dataset_id=ts_id,
            plant_dataset_id=plant_id,
            tag_summaries=result.get("tag_summaries") or [],
            details_by_tag=result.get("details_by_tag") or {},
            monthly_pages_by_tag=result.get("monthly_pages_by_tag") or {},
        )
    except pymysql.Error as e:
        logger.exception("MySQL persist failed (Outlier upload); run continues without DB: %s", e)

    tag_names = [t["tag"] for t in result["tag_summaries"]]
    all_plot_tags = sorted(c for c in df_for_script.columns if c != "Timestamp")
    monthly_pages = result["monthly_pages_by_tag"]
    months_by_tag_idx = [
        [p["month"] for p in monthly_pages.get(s["tag"], [])] for s in result["tag_summaries"]
    ]

    session["last_workflow"] = "outlier"
    session.modified = True

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
        database_enabled=is_configured(),
    )


@bp.route("/docs/workflow-comparison-no-causal-v5-v6.xlsx", methods=["GET"])
def download_workflow_comparison_matrix():
    """Static matrices: logic, signals, class crosswalk, UI mapping, merge how-to."""
    from services.workflow_comparison_excel import build_workbook_bytes

    payload, download_name = build_workbook_bytes()
    return Response(
        payload,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "Cache-Control": "no-cache",
        },
    )


@bp.route("/docs/auto-workflow/<workflow>", methods=["GET"])
def download_auto_workflow_doc(workflow: str):
    """Methodology for auto workflows (parts 4–10): Word .docx, or Word HTML .doc if docx unavailable."""
    key = (workflow or "").strip().lower()
    if key not in AUTO_WORKFLOW_DOCS:
        abort(404)
    filename, body = AUTO_WORKFLOW_DOCS[key]
    from services.methodology_docx import build_methodology_download

    payload, out_filename, mimetype = build_methodology_download(body, filename)
    if out_filename.endswith(".doc"):
        logger.info("Methodology served as Word HTML .doc (install python-docx for native .docx).")
    return Response(
        payload,
        mimetype=mimetype,
        headers={
            "Content-Disposition": f'attachment; filename="{out_filename}"',
            "Cache-Control": "no-cache",
        },
    )


@bp.route("/part4/auto-without-causal", methods=["POST"])
def part4_auto_without_causal():
    drift_xlsx = request.files.get("auto_drift_xlsx")
    if not drift_xlsx:
        return _render_index(active_tab="part4", error="Missing file: auto_drift_xlsx")
    if not getattr(drift_xlsx, "filename", None):
        return _render_index(active_tab="part4", error="Please choose an Excel (.xlsx) file.")
    try:
        validate_excel_filename(drift_xlsx.filename)
    except ValueError as e:
        return _render_index(active_tab="part4", error=str(e))

    try:
        drift_xlsx_path = save_upload_to_temp(drift_xlsx, suffix=".xlsx")
        result = run_auto_without_causal_outlier_drift(drift_xlsx_path)
    except Exception as e:
        logger.exception("Auto (No Causal) upload failed: %s", e)
        return _render_index(
            active_tab="part4",
            error=(
                "Could not process the uploaded file for Auto (No Causal). "
                "Please verify the Excel format (Timestamp + numeric tag columns) and try again."
            ),
        )

    df_for_script = result.pop("df_for_script")
    out_df = result.pop("out_df")
    result_id = uuid.uuid4().hex
    export_payload = {
        "tag_summaries": result.get("tag_summaries") or [],
        "details_by_tag": result.get("details_by_tag") or {},
        "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
    }
    part3_store(result_id, df_for_script, out_df, export_payload=export_payload)

    session["last_workflow"] = "outlier"
    session.modified = True

    return render_template(
        "results.html",
        part2=None,
        part3=None,
        part4={
            "result_id": result_id,
            "summary": result.get("summary") or {},
            "top_tags_by_points": result.get("top_tags_by_points") or [],
            "tag_names": [t.get("tag") for t in (result.get("tag_summaries") or []) if t.get("tag")],
            "all_plot_tags": sorted(c for c in df_for_script.columns if c != "Timestamp"),
            "tag_summaries": result.get("tag_summaries") or [],
            "drift_points_by_tag": {
                str(t.get("tag")): int(t.get("num_drift_points") or 0)
                for t in (result.get("tag_summaries") or [])
                if t.get("tag")
            },
            "details_by_tag": result.get("details_by_tag") or {},
            "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
            "tag_limits_by_tag": result.get("tag_limits_by_tag") or {},
            "x_variables_by_tag": result.get("x_variables_by_tag") or {},
        },
        active_tab="part4",
        database_enabled=is_configured(),
    )


@bp.route("/part5/auto-without-clean-data", methods=["POST"])
def part5_auto_without_clean_data():
    drift_xlsx = request.files.get("auto_no_clean_drift_xlsx")
    if not drift_xlsx:
        return _render_index(active_tab="part5", error="Missing file: auto_no_clean_drift_xlsx")

    drift_xlsx_path = save_upload_to_temp(drift_xlsx, suffix=".xlsx")
    result = run_without_clean_data_outlier_drift(drift_xlsx_path)

    df_for_script = result.pop("df_for_script")
    out_df = result.pop("out_df")
    result_id = uuid.uuid4().hex
    export_payload = {
        "tag_summaries": result.get("tag_summaries") or [],
        "details_by_tag": result.get("details_by_tag") or {},
        "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
    }
    part3_store(result_id, df_for_script, out_df, export_payload=export_payload)

    session["last_workflow"] = "outlier"
    session.modified = True

    return render_template(
        "results.html",
        part2=None,
        part3=None,
        part4={
            "result_id": result_id,
            "summary": result.get("summary") or {},
            "top_tags_by_points": result.get("top_tags_by_points") or [],
            "tag_names": [t.get("tag") for t in (result.get("tag_summaries") or []) if t.get("tag")],
            "all_plot_tags": sorted(c for c in df_for_script.columns if c != "Timestamp"),
            "tag_summaries": result.get("tag_summaries") or [],
            "drift_points_by_tag": {
                str(t.get("tag")): int(t.get("num_drift_points") or 0)
                for t in (result.get("tag_summaries") or [])
                if t.get("tag")
            },
            "details_by_tag": result.get("details_by_tag") or {},
            "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
            "tag_limits_by_tag": result.get("tag_limits_by_tag") or {},
            "x_variables_by_tag": result.get("x_variables_by_tag") or {},
        },
        active_tab="part5",
        database_enabled=is_configured(),
    )


@bp.route("/part6/auto-identification", methods=["POST"])
def part6_auto_identification():
    drift_xlsx = request.files.get("auto_identification_xlsx")
    if not drift_xlsx:
        return _render_index(active_tab="part6", error="Missing file: auto_identification_xlsx")

    drift_xlsx_path = save_upload_to_temp(drift_xlsx, suffix=".xlsx")
    result = run_auto_identification_outlier_drift(drift_xlsx_path)

    df_for_script = result.pop("df_for_script")
    out_df = result.pop("out_df")
    result_id = uuid.uuid4().hex
    export_payload = {
        "tag_summaries": result.get("tag_summaries") or [],
        "details_by_tag": result.get("details_by_tag") or {},
        "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
    }
    part3_store(result_id, df_for_script, out_df, export_payload=export_payload)

    session["last_workflow"] = "outlier"
    session.modified = True

    return render_template(
        "results.html",
        part2=None,
        part3=None,
        part4={
            "result_id": result_id,
            "summary": result.get("summary") or {},
            "top_tags_by_points": result.get("top_tags_by_points") or [],
            "tag_names": [t.get("tag") for t in (result.get("tag_summaries") or []) if t.get("tag")],
            "all_plot_tags": sorted(c for c in df_for_script.columns if c != "Timestamp"),
            "tag_summaries": result.get("tag_summaries") or [],
            "drift_points_by_tag": {
                str(t.get("tag")): int(t.get("num_drift_points") or 0)
                for t in (result.get("tag_summaries") or [])
                if t.get("tag")
            },
            "details_by_tag": result.get("details_by_tag") or {},
            "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
            "tag_limits_by_tag": result.get("tag_limits_by_tag") or {},
            "x_variables_by_tag": result.get("x_variables_by_tag") or {},
        },
        active_tab="part6",
        database_enabled=is_configured(),
    )


@bp.route("/part7/auto-testing-deviation-spike-v4", methods=["POST"])
def part7_auto_testing_deviation_spike_v4():
    drift_xlsx = request.files.get("auto_testing_v4_xlsx")
    if not drift_xlsx:
        return _render_index(active_tab="part7", error="Missing file: auto_testing_v4_xlsx")

    drift_xlsx_path = save_upload_to_temp(drift_xlsx, suffix=".xlsx")
    result = run_testing_deviation_spike_v4_outlier_drift(drift_xlsx_path)

    df_for_script = result.pop("df_for_script")
    out_df = result.pop("out_df")
    result_id = uuid.uuid4().hex
    export_payload = {
        "tag_summaries": result.get("tag_summaries") or [],
        "details_by_tag": result.get("details_by_tag") or {},
        "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
    }
    part3_store(result_id, df_for_script, out_df, export_payload=export_payload)

    session["last_workflow"] = "outlier"
    session.modified = True

    return render_template(
        "results.html",
        part2=None,
        part3=None,
        part4={
            "result_id": result_id,
            "summary": result.get("summary") or {},
            "top_tags_by_points": result.get("top_tags_by_points") or [],
            "tag_names": [t.get("tag") for t in (result.get("tag_summaries") or []) if t.get("tag")],
            "all_plot_tags": sorted(c for c in df_for_script.columns if c != "Timestamp"),
            "tag_summaries": result.get("tag_summaries") or [],
            "drift_points_by_tag": {
                str(t.get("tag")): int(t.get("num_drift_points") or 0)
                for t in (result.get("tag_summaries") or [])
                if t.get("tag")
            },
            "details_by_tag": result.get("details_by_tag") or {},
            "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
            "tag_limits_by_tag": result.get("tag_limits_by_tag") or {},
            "x_variables_by_tag": result.get("x_variables_by_tag") or {},
        },
        active_tab="part7",
        database_enabled=is_configured(),
    )


@bp.route("/part8/auto-testing-deviation-spike-v5", methods=["POST"])
def part8_auto_testing_deviation_spike_v5():
    drift_xlsx = request.files.get("auto_testing_v5_xlsx")
    if not drift_xlsx:
        return _render_index(active_tab="part8", error="Missing file: auto_testing_v5_xlsx")
    if not getattr(drift_xlsx, "filename", None):
        return _render_index(active_tab="part8", error="Please choose an Excel (.xlsx) file.")
    try:
        validate_excel_filename(drift_xlsx.filename)
    except ValueError as e:
        return _render_index(active_tab="part8", error=str(e))

    try:
        drift_xlsx_path = save_upload_to_temp(drift_xlsx, suffix=".xlsx")
        result = run_testing_deviation_spike_v5_outlier_drift(drift_xlsx_path)
    except Exception as e:
        logger.exception("Outlier detection upload failed: %s", e)
        return _render_index(
            active_tab="part8",
            error=(
                "Could not process the uploaded file for Outlier detection. "
                "Please verify the Excel format (Timestamp + numeric tag columns) and try again."
            ),
        )

    df_for_script = result.pop("df_for_script")
    out_df = result.pop("out_df")
    result_id = uuid.uuid4().hex
    export_payload = {
        "tag_summaries": result.get("tag_summaries") or [],
        "details_by_tag": result.get("details_by_tag") or {},
        "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
    }
    part3_store(result_id, df_for_script, out_df, export_payload=export_payload)

    session["last_workflow"] = "outlier"
    session.modified = True

    return render_template(
        "results.html",
        part2=None,
        part3=None,
        part4={
            "result_id": result_id,
            "summary": result.get("summary") or {},
            "top_tags_by_points": result.get("top_tags_by_points") or [],
            "tag_names": [t.get("tag") for t in (result.get("tag_summaries") or []) if t.get("tag")],
            "all_plot_tags": sorted(c for c in df_for_script.columns if c != "Timestamp"),
            "tag_summaries": result.get("tag_summaries") or [],
            "drift_points_by_tag": {
                str(t.get("tag")): int(t.get("num_drift_points") or 0)
                for t in (result.get("tag_summaries") or [])
                if t.get("tag")
            },
            "details_by_tag": result.get("details_by_tag") or {},
            "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
            "tag_limits_by_tag": result.get("tag_limits_by_tag") or {},
            "x_variables_by_tag": result.get("x_variables_by_tag") or {},
        },
        active_tab="part8",
        database_enabled=is_configured(),
    )


@bp.route("/part9/auto-testing-top5-corr-regression-v6", methods=["POST"])
def part9_auto_testing_top5_corr_regression_v6():
    drift_xlsx = request.files.get("auto_testing_v6_xlsx")
    if not drift_xlsx:
        return _render_index(active_tab="part9", error="Missing file: auto_testing_v6_xlsx")
    if not getattr(drift_xlsx, "filename", None):
        return _render_index(active_tab="part9", error="Please choose an Excel (.xlsx) file.")
    try:
        validate_excel_filename(drift_xlsx.filename)
    except ValueError as e:
        return _render_index(active_tab="part9", error=str(e))

    try:
        drift_xlsx_path = save_upload_to_temp(drift_xlsx, suffix=".xlsx")
        result = run_testing_top5_corr_regression_outlier_drift(drift_xlsx_path)
    except Exception as e:
        logger.exception("Outlier detection (using data model) upload failed: %s", e)
        return _render_index(
            active_tab="part9",
            error=(
                "Could not process the uploaded file for Outlier detection (using data model). "
                "Please verify the Excel format (Timestamp + numeric tag columns) and try again."
            ),
        )

    df_for_script = result.pop("df_for_script")
    out_df = result.pop("out_df")
    result_id = uuid.uuid4().hex
    export_payload = {
        "tag_summaries": result.get("tag_summaries") or [],
        "details_by_tag": result.get("details_by_tag") or {},
        "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
    }
    part3_store(result_id, df_for_script, out_df, export_payload=export_payload)

    session["last_workflow"] = "outlier"
    session.modified = True

    return render_template(
        "results.html",
        part2=None,
        part3=None,
        part4={
            "result_id": result_id,
            "summary": result.get("summary") or {},
            "top_tags_by_points": result.get("top_tags_by_points") or [],
            "tag_names": [t.get("tag") for t in (result.get("tag_summaries") or []) if t.get("tag")],
            "all_plot_tags": sorted(c for c in df_for_script.columns if c != "Timestamp"),
            "tag_summaries": result.get("tag_summaries") or [],
            "drift_points_by_tag": {
                str(t.get("tag")): int(t.get("num_drift_points") or 0)
                for t in (result.get("tag_summaries") or [])
                if t.get("tag")
            },
            "details_by_tag": result.get("details_by_tag") or {},
            "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
            "tag_limits_by_tag": result.get("tag_limits_by_tag") or {},
            "x_variables_by_tag": result.get("x_variables_by_tag") or {},
        },
        active_tab="part9",
        database_enabled=is_configured(),
    )


@bp.route("/part10/auto-testing-fusion-v7", methods=["POST"])
def part10_auto_testing_fusion_v7():
    drift_xlsx = request.files.get("auto_testing_v7_xlsx")
    if not drift_xlsx:
        return _render_index(active_tab="part10", error="Missing file: auto_testing_v7_xlsx")

    drift_xlsx_path = save_upload_to_temp(drift_xlsx, suffix=".xlsx")
    result = run_testing_fusion_v7_outlier_drift(drift_xlsx_path)

    df_for_script = result.pop("df_for_script")
    out_df = result.pop("out_df")
    result_id = uuid.uuid4().hex
    export_payload = {
        "tag_summaries": result.get("tag_summaries") or [],
        "details_by_tag": result.get("details_by_tag") or {},
        "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
    }
    part3_store(result_id, df_for_script, out_df, export_payload=export_payload)

    session["last_workflow"] = "outlier"
    session.modified = True

    return render_template(
        "results.html",
        part2=None,
        part3=None,
        part4={
            "result_id": result_id,
            "summary": result.get("summary") or {},
            "top_tags_by_points": result.get("top_tags_by_points") or [],
            "tag_names": [t.get("tag") for t in (result.get("tag_summaries") or []) if t.get("tag")],
            "all_plot_tags": sorted(c for c in df_for_script.columns if c != "Timestamp"),
            "tag_summaries": result.get("tag_summaries") or [],
            "drift_points_by_tag": {
                str(t.get("tag")): int(t.get("num_drift_points") or 0)
                for t in (result.get("tag_summaries") or [])
                if t.get("tag")
            },
            "details_by_tag": result.get("details_by_tag") or {},
            "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
            "tag_limits_by_tag": result.get("tag_limits_by_tag") or {},
            "x_variables_by_tag": result.get("x_variables_by_tag") or {},
        },
        active_tab="part10",
        database_enabled=is_configured(),
    )


@bp.route("/part3/download/<result_id>")
def part3_download_excel(result_id: str):
    ctx = part3_load(result_id)
    if not ctx:
        return _render_index(active_tab="part3", error="Result expired. Re-run Drift Detection to download.")
    payload = ctx.get("export_payload") or {}
    tag_summaries = payload.get("tag_summaries") or []
    details_by_tag = payload.get("details_by_tag") or {}
    monthly_pages_by_tag = payload.get("monthly_pages_by_tag") or {}

    summary_df = pd.DataFrame(tag_summaries)
    detail_rows = []
    for tag, rows in (details_by_tag or {}).items():
        for r in rows or []:
            one = {"Tag": tag}
            one.update(r)
            detail_rows.append(one)
    details_df = pd.DataFrame(detail_rows)

    monthly_rows = []
    for tag, pages in (monthly_pages_by_tag or {}).items():
        for p in pages or []:
            month = p.get("month", "")
            for r in p.get("rows", []) or []:
                one = {"Tag": tag, "Month": month}
                one.update(r)
                monthly_rows.append(one)
    monthly_df = pd.DataFrame(monthly_rows)

    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Tag_Summary")
        details_df.to_excel(writer, index=False, sheet_name="Detail_Rows")
        monthly_df.to_excel(writer, index=False, sheet_name="Monthly_Pages")
    bio.seek(0)
    return send_file(
        bio,
        as_attachment=True,
        download_name=f"drift_detection_result_{result_id}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def register(app):
    app.register_blueprint(bp)
