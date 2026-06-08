"""SQLite persistence for Plant Analysis configurations and classified results."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = APP_ROOT / "plant_analysis_results.db"

STATUS_NORMAL = "Normal"
STATUS_OUTLIER = "Outlier Only"
STATUS_PROCESS = "Process Issue Only"
STATUS_BOTH = "Both"

_TAB_STATUS_MAP = {
    "summary": None,
    "outlier": STATUS_OUTLIER,
    "process": STATUS_PROCESS,
    "both": STATUS_BOTH,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS plant_analysis_configuration (
                id TEXT PRIMARY KEY,
                plant_name TEXT NOT NULL,
                subsystem TEXT NOT NULL,
                dataset_name TEXT NOT NULL,
                config_json TEXT NOT NULL,
                saved_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plant_analysis_run (
                id TEXT PRIMARY KEY,
                configuration_id TEXT NOT NULL,
                plant_name TEXT NOT NULL,
                subsystem TEXT NOT NULL,
                dataset_name TEXT NOT NULL,
                total_tags INTEGER NOT NULL DEFAULT 0,
                total_records INTEGER NOT NULL DEFAULT 0,
                analysis_duration TEXT,
                processed_at TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                FOREIGN KEY (configuration_id) REFERENCES plant_analysis_configuration(id)
            );

            CREATE TABLE IF NOT EXISTS plant_analysis_result_point (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                tag_name TEXT NOT NULL,
                observed_at TEXT,
                tag_value REAL,
                status TEXT NOT NULL,
                outlier_score REAL,
                process_issue_score REAL,
                lower_limit REAL,
                upper_limit REAL,
                related_tags TEXT,
                reason TEXT,
                interpretation TEXT,
                suggested_action TEXT,
                severity TEXT,
                FOREIGN KEY (run_id) REFERENCES plant_analysis_run(id)
            );

            CREATE INDEX IF NOT EXISTS idx_pa_point_run_status
                ON plant_analysis_result_point(run_id, status);
            CREATE INDEX IF NOT EXISTS idx_pa_point_run_tag
                ON plant_analysis_result_point(run_id, tag_name);
            """
        )
        conn.commit()


def save_configuration(
    *,
    plant_name: str,
    subsystem: str,
    dataset_name: str,
    config: Dict[str, Any],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> str:
    init_db(db_path)
    config_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO plant_analysis_configuration
                (id, plant_name, subsystem, dataset_name, config_json, saved_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                config_id,
                plant_name,
                subsystem,
                dataset_name,
                json.dumps(config),
                _utc_now(),
            ),
        )
        conn.commit()
    return config_id


def save_run_with_points(
    *,
    configuration_id: str,
    plant_name: str,
    subsystem: str,
    dataset_name: str,
    total_tags: int,
    total_records: int,
    analysis_duration: str,
    summary: Dict[str, Any],
    points: List[Dict[str, Any]],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> str:
    init_db(db_path)
    run_id = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO plant_analysis_run
                (id, configuration_id, plant_name, subsystem, dataset_name,
                 total_tags, total_records, analysis_duration, processed_at, summary_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                configuration_id,
                plant_name,
                subsystem,
                dataset_name,
                total_tags,
                total_records,
                analysis_duration,
                _utc_now(),
                json.dumps(summary),
            ),
        )
        conn.executemany(
            """
            INSERT INTO plant_analysis_result_point
                (run_id, tag_name, observed_at, tag_value, status, outlier_score,
                 process_issue_score, lower_limit, upper_limit, related_tags, reason,
                 interpretation, suggested_action, severity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    p["tag_name"],
                    p.get("observed_at"),
                    p.get("tag_value"),
                    p["status"],
                    p.get("outlier_score"),
                    p.get("process_issue_score"),
                    p.get("lower_limit"),
                    p.get("upper_limit"),
                    json.dumps(p.get("related_tags") or []),
                    p.get("reason"),
                    p.get("interpretation"),
                    p.get("suggested_action"),
                    p.get("severity"),
                )
                for p in points
            ],
        )
        conn.commit()
    return run_id


def list_runs(db_path: Path | str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, configuration_id, plant_name, subsystem, dataset_name,
                   total_tags, total_records, analysis_duration, processed_at, summary_json
            FROM plant_analysis_run
            ORDER BY processed_at DESC
            """
        ).fetchall()
    return [_row_run(r) for r in rows]


def get_run(run_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM plant_analysis_run WHERE id = ?", (run_id,)
        ).fetchone()
    return _row_run(row) if row else None


def _row_run(row: sqlite3.Row) -> Dict[str, Any]:
    summary = json.loads(row["summary_json"]) if row["summary_json"] else {}
    return {
        "id": row["id"],
        "configuration_id": row["configuration_id"],
        "plant_name": row["plant_name"],
        "subsystem": row["subsystem"],
        "dataset_name": row["dataset_name"],
        "total_tags": row["total_tags"],
        "total_records": row["total_records"],
        "analysis_duration": row["analysis_duration"],
        "processed_at": row["processed_at"],
        "summary": summary,
    }


def query_points(
    *,
    run_id: str,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    severity: Optional[str] = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    init_db(db_path)
    clauses = ["run_id = ?"]
    params: List[Any] = [run_id]
    if status:
        clauses.append("status = ?")
        params.append(status)
    if tag:
        clauses.append("tag_name = ?")
        params.append(tag)
    if date_from:
        clauses.append("observed_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("observed_at <= ?")
        params.append(date_to)
    if severity:
        clauses.append("severity = ?")
        params.append(severity)

    sql = f"""
        SELECT * FROM plant_analysis_result_point
        WHERE {' AND '.join(clauses)}
        ORDER BY observed_at ASC, tag_name ASC
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_point(r) for r in rows]


def _row_point(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "tag_name": row["tag_name"],
        "observed_at": row["observed_at"],
        "tag_value": row["tag_value"],
        "status": row["status"],
        "outlier_score": row["outlier_score"],
        "process_issue_score": row["process_issue_score"],
        "lower_limit": row["lower_limit"],
        "upper_limit": row["upper_limit"],
        "related_tags": json.loads(row["related_tags"] or "[]"),
        "reason": row["reason"],
        "interpretation": row["interpretation"],
        "suggested_action": row["suggested_action"],
        "severity": row["severity"],
    }


def build_summary(run_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    run = get_run(run_id, db_path)
    if not run:
        return {}
    points = query_points(run_id=run_id, db_path=db_path)
    abnormal = [p for p in points if p["status"] != STATUS_NORMAL]

    status_counts = {
        STATUS_NORMAL: 0,
        STATUS_OUTLIER: 0,
        STATUS_PROCESS: 0,
        STATUS_BOTH: 0,
    }
    tag_map: Dict[str, Dict[str, int]] = {}
    for p in points:
        status_counts[p["status"]] = status_counts.get(p["status"], 0) + 1
        tag_entry = tag_map.setdefault(
            p["tag_name"],
            {
                "tag_name": p["tag_name"],
                "total_points": 0,
                "outlier_only": 0,
                "process_issue_only": 0,
                "both": 0,
                "normal": 0,
            },
        )
        tag_entry["total_points"] += 1
        if p["status"] == STATUS_OUTLIER:
            tag_entry["outlier_only"] += 1
        elif p["status"] == STATUS_PROCESS:
            tag_entry["process_issue_only"] += 1
        elif p["status"] == STATUS_BOTH:
            tag_entry["both"] += 1
        else:
            tag_entry["normal"] += 1

    tag_summaries = []
    for row in tag_map.values():
        outlier_exclusive = row["outlier_only"]
        process_exclusive = row["process_issue_only"]
        dual_classified = row["both"]
        tag_summaries.append(
            {
                "tag_name": row["tag_name"],
                "total_points": row["total_points"],
                "outlier": outlier_exclusive,
                "process": process_exclusive,
                "both": outlier_exclusive + process_exclusive,
                "normal": row["normal"],
                "dual_classified": dual_classified,
                "outlier_only": outlier_exclusive,
                "process_issue_only": process_exclusive,
            }
        )
    tag_summaries.sort(key=lambda r: str(r["tag_name"]))

    outlier_exclusive = status_counts.get(STATUS_OUTLIER, 0)
    process_exclusive = status_counts.get(STATUS_PROCESS, 0)
    stored_summary = run.get("summary") or {}

    return {
        "run_id": run_id,
        "plant_name": run["plant_name"],
        "subsystem": run["subsystem"],
        "dataset_name": run["dataset_name"],
        "total_tags_analyzed": run["total_tags"],
        "total_records_processed": run["total_records"],
        "total_outlier_points": outlier_exclusive,
        "total_process_issue_points": process_exclusive,
        "total_abnormal_points": outlier_exclusive + process_exclusive,
        "analysis_duration": run["analysis_duration"],
        "last_processed_at": run["processed_at"],
        "status_distribution": status_counts,
        "tag_summaries": tag_summaries,
        "abnormal_point_count": len(abnormal),
        "engine": stored_summary.get("engine"),
    }


def list_filter_options(db_path: Path | str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        plants = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT plant_name FROM plant_analysis_run ORDER BY plant_name"
            ).fetchall()
        ]
        subsystems = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT subsystem FROM plant_analysis_run ORDER BY subsystem"
            ).fetchall()
        ]
        datasets = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT dataset_name FROM plant_analysis_run ORDER BY dataset_name"
            ).fetchall()
        ]
        runs = list_runs(db_path)
    tags: List[str] = []
    if runs:
        with get_connection(db_path) as conn:
            tags = [
                r[0]
                for r in conn.execute(
                    """
                    SELECT DISTINCT tag_name FROM plant_analysis_result_point
                    WHERE run_id = ?
                    ORDER BY tag_name
                    """,
                    (runs[0]["id"],),
                ).fetchall()
            ]
    return {
        "plants": plants,
        "subsystems": subsystems,
        "datasets": datasets,
        "runs": runs,
        "tags": tags,
    }


def tab_status(tab: str) -> Optional[str]:
    return _TAB_STATUS_MAP.get(tab)
