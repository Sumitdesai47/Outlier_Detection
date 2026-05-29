"""SQLite persistence for rolling outlier detection results."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "rolling_outlier_results.db"


def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rolling_outlier_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                dataset_name TEXT NOT NULL,
                window_mode TEXT NOT NULL,
                window_size INTEGER NOT NULL,
                ts TEXT NOT NULL,
                tag_name TEXT NOT NULL,
                tag_value REAL,
                baseline_mean REAL,
                baseline_std REAL,
                z_score REAL,
                lower_limit REAL,
                upper_limit REAL,
                status TEXT NOT NULL,
                reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rolling_run_tag_ts ON rolling_outlier_results(run_id, tag_name, ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rolling_dataset_created ON rolling_outlier_results(dataset_name, created_at)"
        )
        conn.commit()


def insert_results(
    records: Iterable[Dict[str, Any]],
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    rows = list(records)
    if not rows:
        return 0
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO rolling_outlier_results (
                run_id, dataset_name, window_mode, window_size,
                ts, tag_name, tag_value,
                baseline_mean, baseline_std, z_score,
                lower_limit, upper_limit, status, reason
            )
            VALUES (
                :run_id, :dataset_name, :window_mode, :window_size,
                :ts, :tag_name, :tag_value,
                :baseline_mean, :baseline_std, :z_score,
                :lower_limit, :upper_limit, :status, :reason
            )
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def list_runs(db_path: str | Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT run_id, dataset_name, window_mode, window_size,
                   MIN(ts) AS min_ts, MAX(ts) AS max_ts,
                   COUNT(*) AS rows_count, MAX(created_at) AS created_at
            FROM rolling_outlier_results
            GROUP BY run_id, dataset_name, window_mode, window_size
            ORDER BY created_at DESC
            """
        )
        return [dict(r) for r in cur.fetchall()]


def load_run_results(run_id: str, db_path: str | Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT *
            FROM rolling_outlier_results
            WHERE run_id = ?
            ORDER BY ts ASC, tag_name ASC
            """,
            (run_id,),
        )
        return [dict(r) for r in cur.fetchall()]

