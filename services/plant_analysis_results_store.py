"""SQLite persistence for Plant Analysis configurations and classified results."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

import pandas as pd

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = APP_ROOT / "plant_analysis_results.db"

STATUS_NORMAL = "Normal"
STATUS_OUTLIER = "Outlier Only"
STATUS_PROCESS = "Process Issue Only"
STATUS_BOTH = "Both"

_STRONG_ANOMALY = "Strong Anomaly"

_TAB_STATUS_MAP = {
    "summary": None,
    "outlier": STATUS_OUTLIER,
    "process": STATUS_PROCESS,
    "both": "__abnormal__",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _loads(raw: Any, default: Any) -> Any:
    try:
        if raw in (None, ""):
            return default
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {str(r["name"]) for r in rows}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_live_cache_schema(conn: sqlite3.Connection) -> None:
    """Migrate legacy live cache table (created_at) to current schema (saved_at)."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='plant_analysis_live_cache'"
    ).fetchone()
    if not row:
        return

    columns = {str(r["name"]) for r in conn.execute("PRAGMA table_info(plant_analysis_live_cache)")}
    if "saved_at" not in columns:
        conn.execute("ALTER TABLE plant_analysis_live_cache ADD COLUMN saved_at TEXT")
        if "created_at" in columns:
            conn.execute(
                "UPDATE plant_analysis_live_cache SET saved_at = created_at WHERE saved_at IS NULL"
            )
        else:
            conn.execute(
                "UPDATE plant_analysis_live_cache SET saved_at = datetime('now') WHERE saved_at IS NULL"
            )


def _ensure_live_day_drift_schema(conn: sqlite3.Connection) -> None:
    """Migrate legacy live day drift table (day_iso/tag_name) to current schema."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='plant_analysis_live_day_drift'"
    ).fetchone()
    if not row:
        return

    columns = {str(r["name"]) for r in conn.execute("PRAGMA table_info(plant_analysis_live_day_drift)")}
    if "observation_day" in columns and "tag" in columns:
        return

    conn.execute(
        """
        CREATE TABLE plant_analysis_live_day_drift_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            observation_day TEXT NOT NULL,
            rank INTEGER NOT NULL,
            tag TEXT NOT NULL,
            drift_score REAL NOT NULL
        )
        """
    )
    if "day_iso" in columns and "tag_name" in columns:
        conn.execute(
            """
            INSERT INTO plant_analysis_live_day_drift_new
                (run_id, observation_day, rank, tag, drift_score)
            SELECT run_id, day_iso, rank, tag_name, drift_score
            FROM plant_analysis_live_day_drift
            """
        )
    conn.execute("DROP TABLE plant_analysis_live_day_drift")
    conn.execute("ALTER TABLE plant_analysis_live_day_drift_new RENAME TO plant_analysis_live_day_drift")


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
                final_class TEXT,
                final_status TEXT,
                plot_status TEXT,
                predicted_value REAL,
                s5_peer_fired INTEGER,
                FOREIGN KEY (run_id) REFERENCES plant_analysis_run(id)
            );

            CREATE INDEX IF NOT EXISTS idx_pa_point_run_status
                ON plant_analysis_result_point(run_id, status);
            CREATE INDEX IF NOT EXISTS idx_pa_point_run_tag
                ON plant_analysis_result_point(run_id, tag_name);
            CREATE INDEX IF NOT EXISTS idx_pa_point_run_observed
                ON plant_analysis_result_point(run_id, observed_at);

            CREATE TABLE IF NOT EXISTS plant_analysis_live_cache (
                run_id TEXT PRIMARY KEY,
                wide_json TEXT,
                roots_json TEXT,
                plot_tags_json TEXT,
                saved_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plant_analysis_live_day_drift (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                observation_day TEXT NOT NULL,
                rank INTEGER NOT NULL,
                tag TEXT NOT NULL,
                drift_score REAL NOT NULL
            );
            """
        )
        _ensure_live_cache_schema(conn)
        _ensure_live_day_drift_schema(conn)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pa_live_day_run_day
                ON plant_analysis_live_day_drift(run_id, observation_day)
            """
        )
        _ensure_column(conn, "plant_analysis_result_point", "final_class", "TEXT")
        _ensure_column(conn, "plant_analysis_result_point", "final_status", "TEXT")
        _ensure_column(conn, "plant_analysis_result_point", "plot_status", "TEXT")
        _ensure_column(conn, "plant_analysis_result_point", "predicted_value", "REAL")
        _ensure_column(conn, "plant_analysis_result_point", "s5_peer_fired", "INTEGER")
        _ensure_column(conn, "plant_analysis_result_point", "engines_fired", "TEXT")
        _ensure_column(conn, "plant_analysis_result_point", "reason_short", "TEXT")
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
                json.dumps(summary, default=str),
            ),
        )
        conn.executemany(
            """
            INSERT INTO plant_analysis_result_point
                (run_id, tag_name, observed_at, tag_value, status, outlier_score,
                 process_issue_score, lower_limit, upper_limit, related_tags, reason,
                 interpretation, suggested_action, severity, final_class, final_status,
                 plot_status, predicted_value, s5_peer_fired, engines_fired, reason_short)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    p.get("final_class"),
                    p.get("final_status"),
                    p.get("plot_status"),
                    p.get("predicted_value"),
                    1 if p.get("s5_peer_fired") is True else 0 if p.get("s5_peer_fired") is False else None,
                    json.dumps(p.get("engines_fired") or []),
                    p.get("reason_short"),
                )
                for p in points
            ],
        )
        conn.commit()
    return run_id


def _row_run(row: sqlite3.Row) -> Dict[str, Any]:
    summary = _loads(row["summary_json"], {})
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


def get_run(run_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM plant_analysis_run WHERE id = ?", (run_id,)).fetchone()
    return _row_run(row) if row else None


def list_runs(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    engine: Optional[str] = None,
) -> List[Dict[str, Any]]:
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
    runs = [_row_run(r) for r in rows]
    if engine:
        needle = str(engine).strip().lower()
        runs = [
            r
            for r in runs
            if str((r.get("summary") or {}).get("engine") or "").strip().lower() == needle
        ]
    return runs


def _iso_to_observed_prefix(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return ts.date().isoformat()
    except ValueError:
        return None


def coerce_observation_day(raw: Optional[str]) -> Optional[str]:
    """Normalize calendar day strings to YYYY-MM-DD."""
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    iso = _iso_to_observed_prefix(text)
    if iso:
        return iso
    try:
        ts = pd.to_datetime(text, errors="coerce", dayfirst=False)
        if pd.isna(ts):
            ts = pd.to_datetime(text, errors="coerce", dayfirst=True)
        if pd.isna(ts):
            return None
        return ts.date().isoformat()
    except Exception:
        return None


def normalize_observation_days(days: Sequence[str]) -> List[str]:
    normalized = {coerce_observation_day(day) for day in days}
    return sorted(day for day in normalized if day)


def _day_filter_patterns(day_iso: str) -> List[str]:
    """LIKE prefixes matching the same calendar day across stored timestamp formats."""
    y, m, d = day_iso.split("-")
    m_int, d_int = str(int(m)), str(int(d))
    return [
        f"{day_iso}%",
        f"{m}/{d}/{y}%",
        f"{m_int}/{d_int}/{y}%",
    ]


def _append_observed_day_filter(
    clauses: List[str],
    params: List[Any],
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> None:
    day_iso = coerce_observation_day(date_from) or coerce_observation_day(date_to)
    if not day_iso:
        return
    patterns = _day_filter_patterns(day_iso)
    placeholders = " OR ".join("observed_at LIKE ?" for _ in patterns)
    clauses.append(f"({placeholders})")
    params.extend(patterns)


def observation_days_from_points(points: List[Dict[str, Any]]) -> List[str]:
    days = set()
    for point in points:
        prefix = _iso_to_observed_prefix(point.get("observed_at"))
        if prefix:
            days.add(prefix)
        else:
            coerced = coerce_observation_day(point.get("observed_at"))
            if coerced:
                days.add(coerced)
    return sorted(days)


def query_distinct_observation_days(
    run_id: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> List[str]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT substr(observed_at, 1, 10) AS day
            FROM plant_analysis_result_point
            WHERE run_id = ? AND observed_at IS NOT NULL AND observed_at <> ''
            ORDER BY day ASC
            """,
            (run_id,),
        ).fetchall()
    return normalize_observation_days([str(r["day"]) for r in rows if r["day"]])


def _infer_legacy_plot_status(point: Dict[str, Any]) -> Optional[str]:
    """Infer marker type for rows saved before plot_status/final_class were stored."""
    if point.get("plot_status") or point.get("final_class"):
        return None
    if str(point.get("status") or "") == STATUS_NORMAL:
        return "normal"
    try:
        outlier_score = float(point.get("outlier_score") or 0)
        process_score = float(point.get("process_issue_score") or 0)
    except (TypeError, ValueError):
        return "flagged_unclassified"
    if outlier_score >= 3.5:
        return "strong_outlier"
    if process_score >= 2.5:
        return "sudden_jump"
    return "strong_outlier"


def _enrich_layman_reason(point: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure simple reason text and engine list for legacy rows."""
    if str(point.get("status") or STATUS_NORMAL) == STATUS_NORMAL:
        return point
    try:
        from services.plant_analysis_layman_reason import (
            build_simple_reason_summary,
            extract_failed_engines,
        )

        s5 = point.get("s5_peer_fired")
        if s5 is None:
            s5 = issue_category(point) == "tag"
        row_payload = dict(point)
        legacy_reason = str(point.get("reason") or "").strip()
        if legacy_reason and not row_payload.get("Reason"):
            row_payload["Reason"] = legacy_reason
        if not point.get("engines_fired"):
            point["engines_fired"] = extract_failed_engines(row_payload)
        short = point.get("reason_short")
        if not short or str(short).startswith("[") or "Checks that failed" in str(short):
            short = build_simple_reason_summary(
                tag=str(point.get("tag_name") or ""),
                final_class=str(point.get("final_class") or "Unusual reading"),
                s5_fired=bool(s5),
                row=row_payload,
                actual=point.get("tag_value"),
                predicted=point.get("predicted_value"),
            )
            point["reason_short"] = short
        point["reason"] = point.get("reason_short") or short
    except Exception:
        pass
    return point


def _enrich_plot_status(point: Dict[str, Any]) -> Dict[str, Any]:
    if not point.get("plot_status"):
        if point.get("final_class"):
            from services.plant_analysis_multimodel_runner import final_class_to_plot_status

            point["plot_status"] = final_class_to_plot_status(
                str(point.get("final_class") or ""),
                final_status=point.get("final_status"),
            )
        else:
            legacy = _infer_legacy_plot_status(point)
            if legacy:
                point["plot_status"] = legacy
    return point


def _row_point(row: sqlite3.Row) -> Dict[str, Any]:
    related_tags = _loads(row["related_tags"], [])
    point = {
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
        "related_tags": related_tags if isinstance(related_tags, list) else [],
        "reason": row["reason"],
        "interpretation": row["interpretation"],
        "suggested_action": row["suggested_action"],
        "severity": row["severity"],
        "final_class": row["final_class"],
        "final_status": row["final_status"],
        "plot_status": row["plot_status"],
        "predicted_value": row["predicted_value"],
        "s5_peer_fired": bool(row["s5_peer_fired"]) if row["s5_peer_fired"] is not None else None,
        "engines_fired": _loads(row["engines_fired"], []) if "engines_fired" in row.keys() else [],
        "reason_short": row["reason_short"] if "reason_short" in row.keys() else None,
    }
    if not isinstance(point.get("engines_fired"), list):
        point["engines_fired"] = []
    point = _enrich_plot_status(point)
    return _enrich_layman_reason(point)


def query_points(
    *,
    run_id: str,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    tags: Optional[List[str]] = None,
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
    tag_list = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if tag and tag not in tag_list:
        tag_list.insert(0, str(tag).strip())
    if tag_list:
        placeholders = ", ".join("?" for _ in tag_list)
        clauses.append(f"tag_name IN ({placeholders})")
        params.extend(tag_list)
    elif tag:
        clauses.append("tag_name = ?")
        params.append(tag)

    # Day-level filtering for dashboard date picker (ISO + legacy MM/DD/YYYY timestamps).
    _append_observed_day_filter(clauses, params, date_from=date_from, date_to=date_to)

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


def is_abnormal_point(point: Dict[str, Any]) -> bool:
    return str(point.get("status") or STATUS_NORMAL) != STATUS_NORMAL


def issue_category(point: Dict[str, Any]) -> Optional[str]:
    """
    Classify abnormal points for Result Dashboard tabs.

    tag — outlier detected and S5 peer engine failed (tag issue).
    process — outlier detected and S5 peer engine passed (process issue).
    """
    if not is_abnormal_point(point):
        return None

    s5 = point.get("s5_peer_fired")
    if s5 is True:
        return "tag"
    if s5 is False:
        return "process"

    # Legacy rows saved before s5_peer_fired / corrected status mapping.
    status = str(point.get("status") or "")
    if status == STATUS_BOTH:
        return "tag"
    if status == STATUS_OUTLIER:
        return "process"
    if status == STATUS_PROCESS:
        return "process"
    return "tag"


def point_matches_tab(point: Dict[str, Any], tab: str) -> bool:
    tab_key = str(tab or "summary").strip().lower()
    if tab_key == "summary":
        return False
    if tab_key == "both":
        return is_abnormal_point(point)
    if tab_key == "outlier":
        return issue_category(point) == "tag"
    if tab_key == "process":
        return issue_category(point) == "process"
    return is_abnormal_point(point)


def query_points_for_tab(
    *,
    run_id: str,
    tab: str,
    tag: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    severity: Optional[str] = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    points = query_points(
        run_id=run_id,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
        severity=severity,
        db_path=db_path,
    )
    return [p for p in points if point_matches_tab(p, tab)]


def list_run_tag_names(run_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> List[str]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT tag_name FROM plant_analysis_result_point
            WHERE run_id = ?
            ORDER BY tag_name ASC
            """,
            (run_id,),
        ).fetchall()
    return [str(r[0]) for r in rows if r[0]]


def _normalize_tag_key(tag: str) -> str:
    return str(tag or "").strip().replace("+", " ")


def _x_variables_entries_for_tag(
    x_vars: Dict[str, Any],
    tag: str,
) -> List[Any]:
    if not x_vars or not tag:
        return []
    direct = x_vars.get(str(tag))
    if direct:
        return list(direct)
    normalized = _normalize_tag_key(tag)
    if normalized != str(tag):
        alt = x_vars.get(normalized)
        if alt:
            return list(alt)
    for key, entries in x_vars.items():
        if _normalize_tag_key(str(key)) == normalized:
            return list(entries or [])
    return []


def build_tag_context(
    run_id: str,
    tag: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Model-building tags and full dataset tag list for compare UI."""
    run = get_run(run_id, db_path)
    summary = (run or {}).get("summary") or {}
    meta = summary.get("multimodel_meta") or summary.get("pipeline_meta") or {}
    x_vars = meta.get("x_variables_by_tag") or summary.get("x_variables_by_tag") or {}
    entries = _x_variables_entries_for_tag(x_vars, str(tag))
    model_tags: List[Dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, dict):
            model_tags.append(
                {
                    "tag": str(entry.get("tag") or ""),
                    "corr": entry.get("corr"),
                    "model_importance": entry.get("model_importance"),
                    "group_id": entry.get("group_id"),
                }
            )
        elif entry:
            model_tags.append({"tag": str(entry)})
    model_tags = [m for m in model_tags if m.get("tag")]
    if not model_tags:
        related: List[str] = []
        with get_connection(db_path) as conn:
            rows = conn.execute(
                """
                SELECT related_tags
                FROM plant_analysis_result_point
                WHERE run_id = ? AND tag_name = ? AND related_tags IS NOT NULL AND related_tags <> '[]'
                LIMIT 50
                """,
                (run_id, str(tag)),
            ).fetchall()
        for row in rows:
            for name in _loads(row["related_tags"], []):
                text = str(name or "").strip()
                if text and text != str(tag) and text not in related:
                    related.append(text)
        model_tags = [{"tag": name} for name in sorted(related)]
    dataset_tags = summary.get("dataset_tags") or []
    all_tags = dataset_tags if dataset_tags else list_run_tag_names(run_id, db_path)
    return {
        "tag": str(tag),
        "model_tags": model_tags,
        "all_tags": all_tags,
        "dataset_tags": all_tags,
    }


def build_run_day_meta(run_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    run = get_run(run_id, db_path)
    stored_summary = (run or {}).get("summary") or {}
    pipeline_meta = stored_summary.get("pipeline_meta") or stored_summary.get("multimodel_meta") or {}
    days = normalize_observation_days(
        stored_summary.get("observation_days") or query_distinct_observation_days(run_id, db_path=db_path)
    )
    methodology = stored_summary.get("methodology") or pipeline_meta.get("methodology")
    is_rolling = str(methodology or "").strip().lower() == "rolling_expanding"
    return {
        "observation_days": days,
        "observation_first": days[0] if days else None,
        "observation_last": days[-1] if days else None,
        "selected_day": None if is_rolling else (days[-1] if days else None),
        "methodology": methodology,
        "cooling_period_rows": stored_summary.get("cooling_period_rows")
        or pipeline_meta.get("cooling_period_rows"),
        "analyzed_timestamps": stored_summary.get("analyzed_timestamps")
        or pipeline_meta.get("analyzed_timestamps"),
    }


def build_summary(run_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    run = get_run(run_id, db_path)
    if not run:
        return {}

    points = query_points(run_id=run_id, db_path=db_path)
    abnormal = [p for p in points if p["status"] != STATUS_NORMAL]
    stored_summary = run.get("summary") or {}

    status_counts = {
        STATUS_NORMAL: 0,
        STATUS_OUTLIER: 0,
        STATUS_PROCESS: 0,
        STATUS_BOTH: 0,
    }
    tag_map: Dict[str, Dict[str, int]] = {}

    for point in points:
        status = str(point.get("status") or STATUS_NORMAL)
        status_counts[status] = status_counts.get(status, 0) + 1
        tag = str(point.get("tag_name") or "")
        if not tag:
            continue
        row = tag_map.setdefault(
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
        category = issue_category(point)
        if category == "tag":
            row["outlier_only"] += 1
        elif category == "process":
            row["process_issue_only"] += 1
        elif is_abnormal_point(point):
            row["both"] += 1
        else:
            row["normal"] += 1

    tag_summaries: List[Dict[str, Any]] = []
    for row in tag_map.values():
        outlier_exclusive = row["outlier_only"]
        process_exclusive = row["process_issue_only"]
        dual = row["both"]
        tag_summaries.append(
            {
                "tag_name": row["tag_name"],
                "total_points": row["total_points"],
                "outlier": outlier_exclusive,
                "process": process_exclusive,
                "both": outlier_exclusive + process_exclusive + dual,
                "normal": row["normal"],
                "dual_classified": dual,
                "outlier_only": outlier_exclusive,
                "process_issue_only": process_exclusive,
            }
        )
    tag_summaries.sort(key=lambda r: str(r["tag_name"]))

    day_meta = build_run_day_meta(run_id, db_path=db_path)
    outlier_exclusive = sum(1 for p in points if issue_category(p) == "tag")
    process_exclusive = sum(1 for p in points if issue_category(p) == "process")

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
        "methodology": stored_summary.get("methodology"),
        "cooling_period_rows": stored_summary.get("cooling_period_rows"),
        "analyzed_timestamps": stored_summary.get("analyzed_timestamps"),
        "dataset_tags": stored_summary.get("dataset_tags") or list_run_tag_names(run_id, db_path),
        "x_variables_by_tag": (
            stored_summary.get("x_variables_by_tag")
            or (stored_summary.get("multimodel_meta") or {}).get("x_variables_by_tag")
            or {}
        ),
        **day_meta,
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
    status = _TAB_STATUS_MAP.get(str(tab or "summary").strip().lower())
    if status == "__abnormal__":
        return None
    return status


def effective_point_status(point: Dict[str, Any]) -> str:
    """Map a point to STATUS_OUTLIER (tag issue) or STATUS_PROCESS for plot filtering."""
    category = issue_category(point)
    if category == "tag":
        return STATUS_OUTLIER
    if category == "process":
        return STATUS_PROCESS
    status = str(point.get("status") or STATUS_NORMAL)
    if status == STATUS_BOTH:
        return STATUS_OUTLIER
    return status


def query_slim_series_points(
    run_id: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT tag_name, observed_at, tag_value
            FROM plant_analysis_result_point
            WHERE run_id = ?
            ORDER BY observed_at ASC, tag_name ASC
            """,
            (run_id,),
        ).fetchall()
    return [
        {
            "tag_name": r["tag_name"],
            "observed_at": r["observed_at"],
            "tag_value": r["tag_value"],
        }
        for r in rows
    ]


def _as_day(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return _iso_to_observed_prefix(str(value))


def query_tag_marker_rows(
    run_id: str,
    tag: str,
    *,
    through_day: Optional[date] = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    init_db(db_path)
    clauses = ["run_id = ?", "tag_name = ?"]
    params: List[Any] = [run_id, tag]
    if through_day is not None:
        next_day = through_day + timedelta(days=1)
        clauses.append("observed_at < ?")
        params.append(next_day.isoformat())
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM plant_analysis_result_point
            WHERE {' AND '.join(clauses)}
            ORDER BY observed_at ASC
            """,
            params,
        ).fetchall()
    return [_row_point(r) for r in rows]


def query_has_abnormal_on_day(
    run_id: str,
    day: date,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> bool:
    init_db(db_path)
    prefix = day.isoformat()
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM plant_analysis_result_point
            WHERE run_id = ?
              AND observed_at LIKE ?
              AND status <> ?
            LIMIT 1
            """,
            (run_id, f"{prefix}%", STATUS_NORMAL),
        ).fetchone()
    return row is not None


def query_strong_anomaly_drifts_for_day(
    run_id: str,
    day: date,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    init_db(db_path)
    prefix = day.isoformat()
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT tag_name AS tag, COUNT(*) AS drift_score
            FROM plant_analysis_result_point
            WHERE run_id = ?
              AND observed_at LIKE ?
              AND (final_class = ? OR status = ?)
            GROUP BY tag_name
            ORDER BY drift_score DESC, tag_name ASC
            """,
            (run_id, f"{prefix}%", _STRONG_ANOMALY, _STRONG_ANOMALY),
        ).fetchall()
    return [
        {
            "rank": i + 1,
            "tag": str(r["tag"]),
            "drift_score": float(r["drift_score"]),
        }
        for i, r in enumerate(rows)
    ]


def query_all_strong_anomaly_day_drifts(
    run_id: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> Dict[str, List[Dict[str, Any]]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT substr(observed_at, 1, 10) AS observation_day,
                   tag_name AS tag,
                   COUNT(*) AS drift_score
            FROM plant_analysis_result_point
            WHERE run_id = ?
              AND observed_at IS NOT NULL
              AND observed_at <> ''
              AND (final_class = ? OR status = ?)
            GROUP BY observation_day, tag_name
            ORDER BY observation_day ASC, drift_score DESC, tag_name ASC
            """,
            (run_id, _STRONG_ANOMALY, _STRONG_ANOMALY),
        ).fetchall()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        day = str(row["observation_day"])
        grouped.setdefault(day, []).append(
            {"tag": str(row["tag"]), "drift_score": float(row["drift_score"])}
        )
    ranked: Dict[str, List[Dict[str, Any]]] = {}
    for day, items in grouped.items():
        ranked[day] = [
            {"rank": idx + 1, "tag": item["tag"], "drift_score": item["drift_score"]}
            for idx, item in enumerate(items)
        ]
    return ranked


def save_live_cache(
    run_id: str,
    *,
    wide_json: str,
    roots_json: str,
    plot_tags_json: str,
    day_drifts: Dict[str, List[Dict[str, Any]]],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        columns = {
            str(r["name"])
            for r in conn.execute("PRAGMA table_info(plant_analysis_live_cache)").fetchall()
        }
        now = _utc_now()
        if "created_at" in columns and "saved_at" in columns:
            conn.execute(
                """
                INSERT INTO plant_analysis_live_cache
                    (run_id, wide_json, roots_json, plot_tags_json, created_at, saved_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    wide_json=excluded.wide_json,
                    roots_json=excluded.roots_json,
                    plot_tags_json=excluded.plot_tags_json,
                    created_at=excluded.created_at,
                    saved_at=excluded.saved_at
                """,
                (run_id, wide_json, roots_json, plot_tags_json, now, now),
            )
        elif "created_at" in columns:
            conn.execute(
                """
                INSERT INTO plant_analysis_live_cache
                    (run_id, wide_json, roots_json, plot_tags_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    wide_json=excluded.wide_json,
                    roots_json=excluded.roots_json,
                    plot_tags_json=excluded.plot_tags_json,
                    created_at=excluded.created_at
                """,
                (run_id, wide_json, roots_json, plot_tags_json, now),
            )
        else:
            conn.execute(
                """
                INSERT INTO plant_analysis_live_cache
                    (run_id, wide_json, roots_json, plot_tags_json, saved_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    wide_json=excluded.wide_json,
                    roots_json=excluded.roots_json,
                    plot_tags_json=excluded.plot_tags_json,
                    saved_at=excluded.saved_at
                """,
                (run_id, wide_json, roots_json, plot_tags_json, now),
            )
        conn.execute("DELETE FROM plant_analysis_live_day_drift WHERE run_id = ?", (run_id,))
        for day, rows in (day_drifts or {}).items():
            for row in rows or []:
                conn.execute(
                    """
                    INSERT INTO plant_analysis_live_day_drift
                        (run_id, observation_day, rank, tag, drift_score)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        str(day),
                        int(row.get("rank") or 0),
                        str(row.get("tag") or ""),
                        float(row.get("drift_score") or 0.0),
                    ),
                )
        conn.commit()


def delete_live_cache_for_run(run_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM plant_analysis_live_cache WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM plant_analysis_live_day_drift WHERE run_id = ?", (run_id,))
        conn.commit()


def has_live_cache(run_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> bool:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM plant_analysis_live_cache WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return row is not None


def query_cached_day_drifts(
    run_id: str,
    day: date,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT rank, tag, drift_score
            FROM plant_analysis_live_day_drift
            WHERE run_id = ? AND observation_day = ?
            ORDER BY rank ASC, tag ASC
            """,
            (run_id, day.isoformat()),
        ).fetchall()
    return [
        {"rank": int(r["rank"]), "tag": str(r["tag"]), "drift_score": float(r["drift_score"])}
        for r in rows
    ]


def get_live_cache(
    run_id: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                run_id,
                wide_json,
                roots_json,
                plot_tags_json,
                COALESCE(saved_at, created_at) AS saved_at
            FROM plant_analysis_live_cache
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return None
        drift_rows = conn.execute(
            """
            SELECT observation_day, rank, tag, drift_score
            FROM plant_analysis_live_day_drift
            WHERE run_id = ?
            ORDER BY observation_day ASC, rank ASC
            """,
            (run_id,),
        ).fetchall()
    day_drifts: Dict[str, List[Dict[str, Any]]] = {}
    for drift in drift_rows:
        day = str(drift["observation_day"])
        day_drifts.setdefault(day, []).append(
            {
                "rank": int(drift["rank"]),
                "tag": str(drift["tag"]),
                "drift_score": float(drift["drift_score"]),
            }
        )
    return {
        "run_id": row["run_id"],
        "wide_json": row["wide_json"],
        "roots_json": row["roots_json"],
        "plot_tags_json": row["plot_tags_json"],
        "saved_at": row["saved_at"],
        "day_drifts": day_drifts,
    }
