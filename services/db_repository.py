"""
Persist uploads and run results to MySQL when DATABASE_URL is set (mysql://...).
Same file content (SHA-256) is not inserted twice — existing dataset id is reused.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import secrets
from datetime import datetime
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from pymysql.err import IntegrityError as PyMySQLIntegrityError, OperationalError as PyMySQLOperationalError

from .causal_service import _find_propagation_path_column
from .db_config import ensure_database_exists, get_connection, is_configured
from .time_series_utils import load_wide_time_series_xlsx

logger = logging.getLogger(__name__)

# Set on each causal persist attempt; read by upload API when persist returns None.
LAST_CAUSAL_PERSIST_DIAGNOSTIC: Optional[str] = None

# Short catalog for support / UI (full text is also embedded in LAST_CAUSAL_PERSIST_DIAGNOSTIC).
CAUSAL_PERSIST_ERROR_CATALOG: Dict[str, str] = {
    "CAUSAL_E001": "DATABASE_URL missing or not mysql:// — causal persist skipped.",
    "CAUSAL_E002": "Causal upload body is empty (0 bytes).",
    "CAUSAL_E003": "File is not a readable .xlsx for openpyxl (corrupt or wrong format).",
    "CAUSAL_E004": "MySQL IntegrityError during persist; recovery by hash was attempted.",
    "CAUSAL_E005": "Recovery could not re-open the workbook after IntegrityError.",
    "CAUSAL_E006": "Recovery found no causal_dataset row for this file hash.",
    "CAUSAL_E007": "Recovery completed but causal_row count is still 0.",
    "CAUSAL_E008": "Recovery transaction failed (see message for exception).",
    "CAUSAL_E009": "Unexpected exception during causal persist (see message).",
    "CAUSAL_E010": "Dedupe/re-ingest: existing causal_dataset still has 0 causal_row.",
    "CAUSAL_E011": "Duplicate-hash path: after ingest, causal_row count still 0.",
    "CAUSAL_E012": "New causal_dataset committed but causal_sheet/causal_row empty after retry.",
    "CAUSAL_E013": "Reingest skipped: database not configured.",
    "CAUSAL_E014": "Reingest skipped: empty file bytes.",
    "CAUSAL_E015": "Reingest failed with an exception (see message).",
    "CAUSAL_E016": "Cannot read causal file from disk path (persist_causal_xlsx).",
}

_ROOT = Path(__file__).resolve().parents[1]
_schema_applied = False


def _j(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _schema_sql_files() -> List[Path]:
    return sorted(_ROOT.glob("db/schema/*.sql"))


def _sql_statements(sql_text: str) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        current.append(line)
        if stripped.endswith(";"):
            block = "\n".join(current).strip().rstrip(";").strip()
            if block:
                chunks.append(block)
            current = []
    if current:
        block = "\n".join(current).strip().rstrip(";").strip()
        if block:
            chunks.append(block)
    return chunks


def _executemany_chunked(
    cur: Any, sql: str, rows: List[Tuple[Any, ...]], chunk_size: int = 2000
) -> None:
    for i in range(0, len(rows), chunk_size):
        cur.executemany(sql, rows[i : i + chunk_size])


def apply_schema_if_needed() -> None:
    """Run all db/schema/*.sql files in order (idempotent DDL)."""
    global _schema_applied
    if _schema_applied or not is_configured():
        return
    ensure_database_exists()
    paths = _schema_sql_files()
    if not paths:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            for path in paths:
                if not path.is_file():
                    continue
                sql_text = path.read_text(encoding="utf-8")
                for stmt in _sql_statements(sql_text):
                    try:
                        cur.execute(stmt)
                    except PyMySQLOperationalError as e:
                        # 1061 ER_DUP_KEYNAME — index already exists (re-run migrations).
                        if getattr(e, "args", (None,))[0] == 1061:
                            logger.warning(
                                "schema skip duplicate index (%s): %s",
                                path.name,
                                e.args[1] if len(e.args) > 1 else e,
                            )
                            continue
                        raise
    _schema_applied = True


def _json_safe_cell(v: Any) -> Any:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def persist_timeseries_xlsx(file_path: str, original_filename: str) -> Optional[int]:
    """Load wide XLSX and store dataset + long-form observations. Returns dataset id."""
    if not is_configured():
        return None
    try:
        apply_schema_if_needed()
        content_hash = _sha256_file(file_path)
    except Exception as e:
        logger.exception("timeseries hash/schema failed: %s", e)
        return None

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM timeseries_dataset WHERE content_sha256 = %s",
                    (content_hash,),
                )
                hit = cur.fetchone()
                if hit:
                    logger.info(
                        "timeseries dedupe: reusing dataset id=%s hash=%s...",
                        hit[0],
                        content_hash[:12],
                    )
                    return int(hit[0])
    except Exception as e:
        logger.exception("timeseries dedupe lookup: %s", e)
        return None

    try:
        df = load_wide_time_series_xlsx(file_path, timestamp_col_name="Timestamp")
    except Exception as e:
        logger.exception("timeseries load failed: %s", e)
        return None

    tag_cols = [c for c in df.columns if c not in ("Timestamp", "Timestamp_raw")]
    tag_names = tag_cols
    row_count = len(df)

    observations: List[tuple] = []
    for row_idx, (_, row) in enumerate(df.iterrows()):
        ts = row.get("Timestamp")
        ts_out = None
        if ts is not None and not pd.isna(ts):
            ts_parsed = pd.Timestamp(ts)
            if ts_parsed.tzinfo is None:
                ts_out = ts_parsed.to_pydatetime()
            else:
                ts_out = ts_parsed.tz_convert("UTC").to_pydatetime()
        raw_ts = row.get("Timestamp_raw")
        raw_ts_s = None if raw_ts is None or pd.isna(raw_ts) else str(raw_ts)
        for tag in tag_cols:
            val = row.get(tag)
            # Handle NaN/NaT/blank robustly; avoid float(pd.NaT) TypeError.
            num_v = pd.to_numeric(val, errors="coerce")
            num = None if pd.isna(num_v) else float(num_v)
            observations.append((row_idx, ts_out, raw_ts_s, str(tag), num))

    obs_sql = """
        INSERT INTO timeseries_observation
            (dataset_id, row_index, observed_at, observed_at_raw, tag_name, value)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            observed_at = VALUES(observed_at),
            observed_at_raw = VALUES(observed_at_raw),
            value = VALUES(value)
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO timeseries_dataset
                            (original_filename, timestamp_column, tag_names, row_count, meta, content_sha256)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            original_filename or Path(file_path).name,
                            "Timestamp",
                            _j(tag_names),
                            row_count,
                            _j({"source": "wide_xlsx_upload"}),
                            content_hash,
                        ),
                    )
                    dataset_id = int(cur.lastrowid)
                except PyMySQLIntegrityError:
                    cur.execute(
                        "SELECT id FROM timeseries_dataset WHERE content_sha256 = %s",
                        (content_hash,),
                    )
                    ex = cur.fetchone()
                    if ex:
                        logger.info("timeseries dedupe (race): reusing dataset id=%s", ex[0])
                        return int(ex[0])
                    raise

                obs_rows = [
                    (dataset_id, r, t, tr, tn, v) for r, t, tr, tn, v in observations
                ]
                _executemany_chunked(cur, obs_sql, obs_rows, chunk_size=2000)
        return int(dataset_id)
    except PyMySQLIntegrityError:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM timeseries_dataset WHERE content_sha256 = %s",
                        (content_hash,),
                    )
                    ex = cur.fetchone()
                    if ex:
                        logger.info("timeseries dedupe (race): reusing dataset id=%s", ex[0])
                        return int(ex[0])
        except Exception as e:
            logger.exception("timeseries dedupe after conflict: %s", e)
        return None
    except Exception as e:
        logger.exception("persist_timeseries_xlsx: %s", e)
        return None


def _count_causal_rows_for_dataset(cur, dataset_id: int) -> int:
    cur.execute(
        """
        SELECT COUNT(*) FROM causal_row r
        INNER JOIN causal_sheet s ON s.id = r.sheet_id
        WHERE s.dataset_id = %s
        """,
        (dataset_id,),
    )
    r = cur.fetchone()
    return int(r[0]) if r and r[0] is not None else 0


def _ingest_causal_sheets(cur, dataset_id: int, content: bytes, xl: pd.ExcelFile) -> None:
    """Insert causal_sheet + causal_row for each non-empty sheet (matches parse_causal_matrix_xlsx)."""
    row_sql = """
        INSERT INTO causal_row (sheet_id, excel_row_number, propagation_path, row_payload)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            propagation_path = VALUES(propagation_path),
            row_payload = VALUES(row_payload)
    """
    for sheet_name in xl.sheet_names:
        df = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name, engine="openpyxl")
        if df.empty:
            continue
        path_col = None
        try:
            path_col = _find_propagation_path_column(df)
        except Exception:
            path_col = df.columns[0] if len(df.columns) else None

        cur.execute(
            """
            INSERT INTO causal_sheet (dataset_id, sheet_name, row_count)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE row_count = VALUES(row_count)
            """,
            (dataset_id, str(sheet_name), len(df)),
        )
        cur.execute(
            "SELECT id FROM causal_sheet WHERE dataset_id = %s AND sheet_name = %s",
            (dataset_id, str(sheet_name)),
        )
        sheet_row = cur.fetchone()
        if not sheet_row:
            continue
        sheet_id = int(sheet_row[0])

        rows_buf: List[tuple] = []
        for i, (_, row) in enumerate(df.iterrows(), start=2):
            payload = {str(k): _json_safe_cell(row[k]) for k in df.columns}
            ptxt = None
            if path_col is not None and path_col in df.columns:
                v = row.get(path_col)
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    ptxt = str(v).strip() or None
            rows_buf.append((sheet_id, i, ptxt, _j(payload)))

        if rows_buf:
            _executemany_chunked(cur, row_sql, rows_buf, chunk_size=500)


def persist_causal_workbook_bytes(
    content: bytes,
    original_filename: str,
    *,
    reuse_by_content_hash: bool = True,
) -> Optional[int]:
    """
    Store causal workbook from raw bytes: causal_dataset + causal_sheet + causal_row.

    Uses the same BytesIO + openpyxl path as parse_causal_matrix_xlsx so ingestion matches validation.

    If content_sha256 matches an existing row but causal_row is empty (failed prior ingest),
    sheets are cleared and the workbook is re-ingested into that dataset id.

    When reuse_by_content_hash is True (default), one causal_dataset row is shared for identical
    file bytes (UNIQUE content_sha256). When False (e.g. plant upload), always INSERT a new
    causal_dataset using a unique storage hash; the file's SHA-256 is stored in
    meta.source_content_sha256.

    On failure, see LAST_CAUSAL_PERSIST_DIAGNOSTIC (error codes CAUSAL_E001…).
    """
    global LAST_CAUSAL_PERSIST_DIAGNOSTIC
    LAST_CAUSAL_PERSIST_DIAGNOSTIC = None

    if not is_configured():
        LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
            "CAUSAL_E001: DATABASE_URL not set or not mysql:// — MySQL unavailable."
        )
        return None
    if not content:
        LAST_CAUSAL_PERSIST_DIAGNOSTIC = "CAUSAL_E002: Causal file body is empty (0 bytes)."
        return None
    try:
        apply_schema_if_needed()
        file_sha256 = hashlib.sha256(content).hexdigest()
        xl = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
    except Exception as e:
        LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
            f"CAUSAL_E003: Cannot open workbook as .xlsx (openpyxl). {type(e).__name__}: {e}"
        )
        logger.exception("causal workbook open/hash failed: %s", e)
        return None

    if reuse_by_content_hash:
        row_sha256 = file_sha256
    else:
        row_sha256 = hashlib.sha256(
            content + b"\x00plant_dataset\x00" + secrets.token_bytes(16)
        ).hexdigest()

    orig = (original_filename or "causal.xlsx").strip() or "causal.xlsx"
    meta_obj: Dict[str, Any] = {"sheets": xl.sheet_names}
    if not reuse_by_content_hash:
        meta_obj["source_content_sha256"] = file_sha256
        meta_obj["persist_mode"] = "plant_unique_causal_dataset"
    meta = _j(meta_obj)

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if reuse_by_content_hash:
                    cur.execute(
                        "SELECT id FROM causal_dataset WHERE content_sha256 = %s",
                        (row_sha256,),
                    )
                    hit = cur.fetchone()
                    if hit:
                        dataset_id = int(hit[0])
                        n = _count_causal_rows_for_dataset(cur, dataset_id)
                        if n > 0:
                            logger.info(
                                "causal dedupe: reusing dataset id=%s hash=%s...",
                                dataset_id,
                                row_sha256[:12],
                            )
                            return dataset_id
                        logger.warning(
                            "causal dedupe hit id=%s but 0 rows; re-ingesting sheets",
                            dataset_id,
                        )
                        cur.execute(
                            "DELETE FROM causal_sheet WHERE dataset_id = %s", (dataset_id,)
                        )
                        cur.execute(
                            """
                            UPDATE causal_dataset
                            SET meta = %s, original_filename = %s
                            WHERE id = %s
                            """,
                            (meta, orig, dataset_id),
                        )
                        _ingest_causal_sheets(cur, dataset_id, content, xl)
                        if _count_causal_rows_for_dataset(cur, dataset_id) == 0:
                            LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
                                f"CAUSAL_E010: After re-ingest, causal_dataset id={dataset_id} "
                                "still has 0 causal_row (all sheets empty for pandas read_excel?)."
                            )
                            logger.error(
                                "causal re-ingest still 0 rows for dataset_id=%s", dataset_id
                            )
                        else:
                            LAST_CAUSAL_PERSIST_DIAGNOSTIC = None
                        return dataset_id

                try:
                    cur.execute(
                        """
                        INSERT INTO causal_dataset (original_filename, meta, content_sha256)
                        VALUES (%s, %s, %s)
                        """,
                        (orig, meta, row_sha256),
                    )
                    dataset_id = int(cur.lastrowid)
                except PyMySQLIntegrityError:
                    cur.execute(
                        "SELECT id FROM causal_dataset WHERE content_sha256 = %s",
                        (row_sha256,),
                    )
                    ex = cur.fetchone()
                    if not ex:
                        raise
                    dataset_id = int(ex[0])
                    n = _count_causal_rows_for_dataset(cur, dataset_id)
                    if n == 0:
                        cur.execute(
                            "DELETE FROM causal_sheet WHERE dataset_id = %s",
                            (dataset_id,),
                        )
                        cur.execute(
                            """
                            UPDATE causal_dataset
                            SET meta = %s, original_filename = %s
                            WHERE id = %s
                            """,
                            (meta, orig, dataset_id),
                        )
                        _ingest_causal_sheets(cur, dataset_id, content, xl)
                    if _count_causal_rows_for_dataset(cur, dataset_id) == 0:
                        LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
                            f"CAUSAL_E011: Duplicate hash path — causal_dataset id={dataset_id} "
                            "still has 0 causal_row after ingest."
                        )
                        logger.error(
                            "causal ingest after duplicate-key still 0 rows for dataset_id=%s",
                            dataset_id,
                        )
                    else:
                        LAST_CAUSAL_PERSIST_DIAGNOSTIC = None
                    return dataset_id

                _ingest_causal_sheets(cur, dataset_id, content, xl)
                n_final = _count_causal_rows_for_dataset(cur, dataset_id)
                if n_final == 0:
                    logger.warning(
                        "causal ingest produced 0 rows for new dataset_id=%s; re-trying ingest",
                        dataset_id,
                    )
                    cur.execute(
                        "DELETE FROM causal_sheet WHERE dataset_id = %s", (dataset_id,)
                    )
                    _ingest_causal_sheets(cur, dataset_id, content, xl)
                    n_final = _count_causal_rows_for_dataset(cur, dataset_id)
                    if n_final == 0:
                        LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
                            f"CAUSAL_E012: New causal_dataset id={dataset_id} committed but "
                            "causal_sheet/causal_row remain empty (check sheet visibility, filters, macros)."
                        )
                    else:
                        LAST_CAUSAL_PERSIST_DIAGNOSTIC = None
                return dataset_id
    except PyMySQLIntegrityError as ie:
        # e.g. race on content_sha256, or rare duplicate inside _ingest_causal_sheets.
        # Never return an id without verifying causal_row exists.
        logger.warning("persist_causal_workbook_bytes integrity (recovering): %s", ie)
        LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
            f"CAUSAL_E004: MySQL IntegrityError during causal persist — {ie!r}. "
            "Attempting recovery by content_sha256."
        )
        try:
            xl_recover = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
        except Exception as e2:
            LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
                f"CAUSAL_E005: Recovery failed — cannot re-open xlsx. {type(e2).__name__}: {e2}"
            )
            return None
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM causal_dataset WHERE content_sha256 = %s",
                        (row_sha256,),
                    )
                    ex = cur.fetchone()
                    if not ex:
                        LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
                            "CAUSAL_E006: Recovery — no causal_dataset row for this file hash after error."
                        )
                        return None
                    did = int(ex[0])
                    n = _count_causal_rows_for_dataset(cur, did)
                    if n == 0:
                        cur.execute(
                            "DELETE FROM causal_sheet WHERE dataset_id = %s", (did,)
                        )
                        cur.execute(
                            """
                            UPDATE causal_dataset
                            SET meta = %s, original_filename = %s
                            WHERE id = %s
                            """,
                            (meta, orig, did),
                        )
                        _ingest_causal_sheets(cur, did, content, xl_recover)
                    n2 = _count_causal_rows_for_dataset(cur, did)
                    if n2 == 0:
                        LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
                            f"CAUSAL_E007: Recovery finished but causal_dataset id={did} has 0 causal_row."
                        )
                    else:
                        LAST_CAUSAL_PERSIST_DIAGNOSTIC = None
                    return did
        except Exception as e:
            LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
                f"CAUSAL_E008: Recovery transaction failed — {type(e).__name__}: {e}"
            )
            logger.exception("causal dedupe after conflict: %s", e)
            return None
    except Exception as e:
        LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
            f"CAUSAL_E009: Unexpected error during causal persist — {type(e).__name__}: {e}"
        )
        logger.exception("persist_causal_workbook_bytes: %s", e)
        return None


def persist_causal_xlsx(
    file_path: str,
    original_filename: str,
    *,
    reuse_by_content_hash: bool = True,
) -> Optional[int]:
    """Store causal workbook from disk path (delegates to byte ingest)."""
    global LAST_CAUSAL_PERSIST_DIAGNOSTIC
    if not is_configured():
        LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
            "CAUSAL_E001: DATABASE_URL not set or not mysql:// — MySQL unavailable."
        )
        return None
    try:
        data = Path(file_path).read_bytes()
    except Exception as e:
        LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
            f"CAUSAL_E016: Cannot read causal file from disk — {type(e).__name__}: {e}"
        )
        logger.exception("causal xlsx read failed: %s", e)
        return None
    return persist_causal_workbook_bytes(
        data,
        original_filename or Path(file_path).name,
        reuse_by_content_hash=reuse_by_content_hash,
    )


def count_causal_rows_for_causal_dataset(dataset_id: int) -> int:
    """Number of causal_row rows under this causal_dataset.id."""
    if not is_configured():
        return 0
    try:
        apply_schema_if_needed()
        with get_connection() as conn:
            with conn.cursor() as cur:
                return _count_causal_rows_for_dataset(cur, int(dataset_id))
    except Exception as e:
        logger.exception("count_causal_rows_for_causal_dataset: %s", e)
        return 0


def reingest_causal_workbook_for_dataset_id(
    dataset_id: int, content: bytes, original_filename: str
) -> Optional[str]:
    """
    Replace causal_sheet/causal_row for an existing causal_dataset id (same file bytes).

    Returns None on success, or a CAUSAL_E013…E015 diagnostic string on failure.
    """
    global LAST_CAUSAL_PERSIST_DIAGNOSTIC
    if not is_configured():
        LAST_CAUSAL_PERSIST_DIAGNOSTIC = (
            "CAUSAL_E013: Reingest skipped — DATABASE_URL not set or not mysql://."
        )
        return LAST_CAUSAL_PERSIST_DIAGNOSTIC
    if not content:
        LAST_CAUSAL_PERSIST_DIAGNOSTIC = "CAUSAL_E014: Reingest skipped — causal file body is empty."
        return LAST_CAUSAL_PERSIST_DIAGNOSTIC
    try:
        apply_schema_if_needed()
        orig = (original_filename or "causal.xlsx").strip() or "causal.xlsx"
        xl = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
        meta = _j({"sheets": xl.sheet_names})
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM causal_sheet WHERE dataset_id = %s", (int(dataset_id),)
                )
                cur.execute(
                    """
                    UPDATE causal_dataset
                    SET meta = %s, original_filename = %s
                    WHERE id = %s
                    """,
                    (meta, orig, int(dataset_id)),
                )
                _ingest_causal_sheets(cur, int(dataset_id), content, xl)
    except Exception as e:
        msg = f"CAUSAL_E015: Reingest failed — {type(e).__name__}: {e}"
        LAST_CAUSAL_PERSIST_DIAGNOSTIC = msg
        logger.exception("reingest_causal_workbook_for_dataset_id: %s", e)
        return msg
    LAST_CAUSAL_PERSIST_DIAGNOSTIC = None
    return None


def persist_anomaly_run(
    *,
    result_session_uuid: str,
    timeseries_dataset_id: Optional[int],
    causal_dataset_id: Optional[int],
    plant_dataset_id: Optional[int],
    historic_ratio: float,
    lookback_months: int,
    top_k_drift: int,
    summary: Dict[str, Any],
    top_drift_rows: List[Dict[str, Any]],
) -> Optional[int]:
    if not is_configured():
        return None
    drift_sql = """
        INSERT INTO anomaly_drift_result (run_id, plant_dataset_id, rank_order, tag, drift_score)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            tag = VALUES(tag),
            drift_score = VALUES(drift_score),
            plant_dataset_id = VALUES(plant_dataset_id)
    """
    try:
        apply_schema_if_needed()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO anomaly_run (
                        result_session_uuid, timeseries_dataset_id, causal_dataset_id, plant_dataset_id,
                        historic_ratio, lookback_months, top_k_drift, summary, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'completed')
                    """,
                    (
                        result_session_uuid,
                        timeseries_dataset_id,
                        causal_dataset_id,
                        plant_dataset_id,
                        historic_ratio,
                        lookback_months,
                        top_k_drift,
                        _j(summary),
                    ),
                )
                run_id = int(cur.lastrowid)

                drift_tuples = [
                    (
                        run_id,
                        plant_dataset_id,
                        i,
                        str(r.get("Tag", "")),
                        r.get("Drift_Score"),
                    )
                    for i, r in enumerate(top_drift_rows)
                ]
                if drift_tuples:
                    _executemany_chunked(cur, drift_sql, drift_tuples, chunk_size=500)
        return int(run_id)
    except Exception as e:
        logger.exception("persist_anomaly_run: %s", e)
        return None


def persist_anomaly_roots(
    *,
    result_session_uuid: str,
    target_tag: str,
    rows: List[Dict[str, Any]],
) -> None:
    if not is_configured() or not rows:
        return
    insert_sql = """
        INSERT INTO anomaly_root_cause_result
            (run_id, plant_dataset_id, target_tag, rank_order, root_cause_tag, root_cause_score, propagation_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    try:
        apply_schema_if_needed()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, plant_dataset_id FROM anomaly_run WHERE result_session_uuid = %s",
                    (result_session_uuid,),
                )
                one = cur.fetchone()
                if not one:
                    return
                run_id = one[0]
                plant_dataset_id = one[1]
                cur.execute(
                    "DELETE FROM anomaly_root_cause_result WHERE run_id = %s AND target_tag = %s",
                    (run_id, target_tag),
                )
                tuples = [
                    (
                        run_id,
                        plant_dataset_id,
                        target_tag,
                        i,
                        str(r.get("root_cause", "")),
                        r.get("root_cause_score"),
                        str(r.get("propagation_path", "")),
                    )
                    for i, r in enumerate(rows)
                ]
                _executemany_chunked(cur, insert_sql, tuples, chunk_size=500)
    except Exception as e:
        logger.exception("persist_anomaly_roots: %s", e)


def persist_outlier_run(
    *,
    result_session_uuid: str,
    timeseries_dataset_id: Optional[int],
    plant_dataset_id: Optional[int],
    tag_summaries: List[Dict[str, Any]],
    details_by_tag: Dict[str, Any],
    monthly_pages_by_tag: Dict[str, List[Dict[str, Any]]],
) -> Optional[int]:
    if not is_configured():
        return None
    month_sql = """
        INSERT INTO outlier_monthly_page (run_id, plant_dataset_id, tag_name, month_label, page_rows)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            page_rows = VALUES(page_rows),
            plant_dataset_id = VALUES(plant_dataset_id)
    """
    try:
        apply_schema_if_needed()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO outlier_run
                        (result_session_uuid, timeseries_dataset_id, plant_dataset_id, tag_summaries, details_by_tag, status)
                    VALUES (%s, %s, %s, %s, %s, 'completed')
                    """,
                    (
                        result_session_uuid,
                        timeseries_dataset_id,
                        plant_dataset_id,
                        _j(tag_summaries),
                        _j(details_by_tag),
                    ),
                )
                run_id = int(cur.lastrowid)

                month_rows: List[tuple] = []
                for tag, pages in (monthly_pages_by_tag or {}).items():
                    for p in pages:
                        month_rows.append(
                            (
                                run_id,
                                plant_dataset_id,
                                str(tag),
                                str(p.get("month", "")),
                                _j(p.get("rows", [])),
                            )
                        )
                if month_rows:
                    _executemany_chunked(cur, month_sql, month_rows, chunk_size=500)
        return int(run_id)
    except Exception as e:
        logger.exception("persist_outlier_run: %s", e)
        return None


def delete_scheduled_jobs_from_hour_bucket_onwards(hour_bucket: datetime) -> int:
    """Remove scheduled rows with hour_bucket >= that UTC day start (cascades drift/root)."""
    if not is_configured():
        return 0
    day = hour_bucket.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        apply_schema_if_needed()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM scheduled_anomaly_job WHERE hour_bucket >= %s",
                    (day,),
                )
                deleted = int(cur.rowcount or 0)
        return deleted
    except Exception as e:
        logger.exception("delete_scheduled_jobs_from_hour_bucket_onwards: %s", e)
        return 0


def scheduled_try_start_job(
    hour_bucket: datetime,
    timeseries_dataset_id: Optional[int],
    causal_dataset_id: Optional[int],
    plant_dataset_id: int,
) -> Optional[int]:
    """
    Create or reuse a row for (plant_dataset_id, hour_bucket) with status 'running'.
    Returns None if this bucket is already completed/skipped (no work).
    """
    if not is_configured():
        return None

    def _reset_to_running(cur: Any, jid: int) -> None:
        cur.execute("DELETE FROM scheduled_anomaly_drift WHERE job_id = %s", (jid,))
        cur.execute("DELETE FROM scheduled_anomaly_root WHERE job_id = %s", (jid,))
        cur.execute(
            """
            UPDATE scheduled_anomaly_job
            SET status = 'running', error_message = NULL, summary = NULL,
                finished_at = NULL, timeseries_dataset_id = %s, causal_dataset_id = %s,
                plant_dataset_id = %s
            WHERE id = %s
            """,
            (timeseries_dataset_id, causal_dataset_id, plant_dataset_id, jid),
        )

    try:
        apply_schema_if_needed()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, status FROM scheduled_anomaly_job
                    WHERE hour_bucket = %s AND plant_dataset_id = %s
                    """,
                    (hour_bucket, plant_dataset_id),
                )
                ex = cur.fetchone()
                if ex:
                    jid, st = int(ex[0]), str(ex[1])
                    if st == "completed":
                        return None
                    if st == "running":
                        cur.execute(
                            """
                            SELECT TIMESTAMPDIFF(MINUTE, created_at, NOW(6))
                            FROM scheduled_anomaly_job WHERE id = %s
                            """,
                            (jid,),
                        )
                        age = cur.fetchone()
                        age_m = int(age[0]) if age and age[0] is not None else 9999
                        if age_m < 120:
                            return None
                        _reset_to_running(cur, jid)
                        conn.commit()
                        return jid
                    _reset_to_running(cur, jid)
                    conn.commit()
                    return jid

                try:
                    cur.execute(
                        """
                        INSERT INTO scheduled_anomaly_job
                            (hour_bucket, timeseries_dataset_id, causal_dataset_id,
                             plant_dataset_id, status)
                        VALUES (%s, %s, %s, %s, 'running')
                        """,
                        (
                            hour_bucket,
                            timeseries_dataset_id,
                            causal_dataset_id,
                            plant_dataset_id,
                        ),
                    )
                    conn.commit()
                    return int(cur.lastrowid)
                except PyMySQLIntegrityError:
                    conn.rollback()
                    cur.execute(
                        """
                        SELECT id, status FROM scheduled_anomaly_job
                        WHERE hour_bucket = %s AND plant_dataset_id = %s
                        """,
                        (hour_bucket, plant_dataset_id),
                    )
                    ex2 = cur.fetchone()
                    if not ex2:
                        raise
                    jid2, st2 = int(ex2[0]), str(ex2[1])
                    if st2 == "completed":
                        return None
                    if st2 == "running":
                        cur.execute(
                            """
                            SELECT TIMESTAMPDIFF(MINUTE, created_at, NOW(6))
                            FROM scheduled_anomaly_job WHERE id = %s
                            """,
                            (jid2,),
                        )
                        age = cur.fetchone()
                        age_m = int(age[0]) if age and age[0] is not None else 9999
                        if age_m < 120:
                            return None
                        _reset_to_running(cur, jid2)
                        conn.commit()
                        return jid2
                    _reset_to_running(cur, jid2)
                    conn.commit()
                    return jid2
    except Exception as e:
        logger.exception("scheduled_try_start_job: %s", e)
        return None


def scheduled_finish_job_success(
    job_id: int,
    summary: Dict[str, Any],
    drift_rows: List[Dict[str, Any]],
) -> None:
    if not is_configured():
        return
    drift_sql = """
        INSERT INTO scheduled_anomaly_drift (job_id, rank_order, tag, drift_score)
        VALUES (%s, %s, %s, %s)
    """
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM scheduled_anomaly_drift WHERE job_id = %s", (job_id,))
                    tuples = [
                        (job_id, i, str(r.get("Tag", "")), r.get("Drift_Score"))
                        for i, r in enumerate(drift_rows)
                    ]
                    if tuples:
                        _executemany_chunked(cur, drift_sql, tuples, chunk_size=500)
                    cur.execute(
                        """
                        UPDATE scheduled_anomaly_job
                        SET status = 'completed', summary = %s, error_message = NULL,
                            finished_at = CURRENT_TIMESTAMP(6)
                        WHERE id = %s
                        """,
                        (_j(summary), job_id),
                    )
                conn.commit()
            return
        except PyMySQLOperationalError as e:
            if int(getattr(e, "args", [None])[0] or 0) in (1205, 1213) and attempt < max_attempts:
                backoff = 0.35 * attempt
                logger.warning(
                    "scheduled_finish_job_success lock conflict (attempt %s/%s, retry in %.2fs): %s",
                    attempt,
                    max_attempts,
                    backoff,
                    e,
                )
                time.sleep(backoff)
                continue
            logger.exception("scheduled_finish_job_success: %s", e)
            return
        except Exception as e:
            logger.exception("scheduled_finish_job_success: %s", e)
            return


def scheduled_replace_roots(job_id: int, roots_by_tag: Dict[str, List[Dict[str, Any]]]) -> None:
    if not is_configured():
        return
    ins_sql = """
        INSERT INTO scheduled_anomaly_root
            (job_id, target_tag, rank_order, root_cause_tag, root_cause_score, propagation_path)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM scheduled_anomaly_root WHERE job_id = %s", (job_id,))
                    tuples: List[tuple] = []
                    for tag, rows in (roots_by_tag or {}).items():
                        for i, r in enumerate(rows or []):
                            tuples.append(
                                (
                                    job_id,
                                    str(tag),
                                    i,
                                    str(r.get("root_cause", "")),
                                    r.get("root_cause_score"),
                                    str(r.get("propagation_path", "")),
                                )
                            )
                    if tuples:
                        _executemany_chunked(cur, ins_sql, tuples, chunk_size=500)
                conn.commit()
            return
        except PyMySQLOperationalError as e:
            if int(getattr(e, "args", [None])[0] or 0) in (1205, 1213) and attempt < max_attempts:
                backoff = 0.35 * attempt
                logger.warning(
                    "scheduled_replace_roots lock conflict (attempt %s/%s, retry in %.2fs): %s",
                    attempt,
                    max_attempts,
                    backoff,
                    e,
                )
                time.sleep(backoff)
                continue
            logger.exception("scheduled_replace_roots: %s", e)
            return
        except Exception as e:
            logger.exception("scheduled_replace_roots: %s", e)
            return


def scheduled_finish_job_skipped(job_id: int, message: str) -> None:
    if not is_configured():
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE scheduled_anomaly_job
                    SET status = 'skipped', error_message = %s, summary = NULL,
                        finished_at = CURRENT_TIMESTAMP(6)
                    WHERE id = %s
                    """,
                    (message[:2000], job_id),
                )
            conn.commit()
    except Exception as e:
        logger.exception("scheduled_finish_job_skipped: %s", e)


def scheduled_finish_job_failed(job_id: int, message: str) -> None:
    if not is_configured():
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE scheduled_anomaly_job
                    SET status = 'failed', error_message = %s,
                        finished_at = CURRENT_TIMESTAMP(6)
                    WHERE id = %s
                    """,
                    (message[:4000], job_id),
                )
            conn.commit()
    except Exception as e:
        logger.exception("scheduled_finish_job_failed: %s", e)
