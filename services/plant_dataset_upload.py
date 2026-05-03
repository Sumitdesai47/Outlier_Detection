"""
Transactional insert: plant_dataset + time_series_data + causal_data.

All inserts run in one DB transaction; any failure rolls back.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Tuple

from pymysql.err import IntegrityError as PyMySQLIntegrityError

from .db_config import get_connection, is_configured
from .db_repository import _executemany_chunked, apply_schema_if_needed
from . import db_repository as db_repo
from .dataset_upload_parse import parse_causal_matrix_xlsx, parse_timeseries_xlsx

logger = logging.getLogger(__name__)


def _j(row: Dict[str, Any]) -> str:
    return json.dumps(row, default=str)


def insert_plant_upload_transaction(
    plant_name: str,
    timeseries_xlsx_bytes: bytes,
    causal_xlsx_bytes: bytes,
    causal_filename: str,
    timeseries_filename: str,
) -> Dict[str, Any]:
    """
    Validate plant name, parse workbooks, persist datasets, and upsert plant mapping.

    Returns dict: success (bool), message (str), dataset_id (int|None), error_code (str|None).
    """
    if not is_configured():
        return {
            "success": False,
            "message": "Database is not configured (set DATABASE_URL).",
            "dataset_id": None,
            "error_code": "no_database",
        }

    plant_name = (plant_name or "").strip()
    if not plant_name:
        return {
            "success": False,
            "message": "Plant name is required.",
            "dataset_id": None,
            "error_code": "validation",
        }

    try:
        ts_rows = parse_timeseries_xlsx(timeseries_xlsx_bytes)
        causal_rows = parse_causal_matrix_xlsx(causal_xlsx_bytes)
    except ValueError as e:
        return {
            "success": False,
            "message": str(e),
            "dataset_id": None,
            "error_code": "validation",
        }

    apply_schema_if_needed()

    insert_plant = "INSERT INTO plant_dataset (plant_name) VALUES (%s)"
    insert_ts = """
        INSERT INTO time_series_data (dataset_id, row_index, row_data)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE row_data = VALUES(row_data)
    """
    insert_causal = """
        INSERT INTO causal_data (dataset_id, sheet_name, row_index, row_data)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE row_data = VALUES(row_data)
    """

    ts_tmp = None
    ts_dataset_id = None
    causal_dataset_id = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tf:
            tf.write(timeseries_xlsx_bytes)
            ts_tmp = tf.name
        ts_dataset_id = db_repo.persist_timeseries_xlsx(
            ts_tmp, timeseries_filename or "timeseries.xlsx"
        )
        # Same bytes + openpyxl path as parse_causal_matrix_xlsx (avoids temp-file / engine mismatch).
        # One causal_dataset row per plant upload (same file bytes otherwise dedupe globally).
        causal_dataset_id = db_repo.persist_causal_workbook_bytes(
            causal_xlsx_bytes,
            causal_filename or "causal.xlsx",
            reuse_by_content_hash=False,
        )
    finally:
        if ts_tmp and os.path.exists(ts_tmp):
            try:
                os.remove(ts_tmp)
            except OSError:
                pass

    if not ts_dataset_id or not causal_dataset_id:
        parts = [
            "Could not persist uploaded files into timeseries_dataset / causal_dataset.",
        ]
        if not ts_dataset_id:
            parts.append("Timeseries workbook did not yield a timeseries_dataset id.")
        if not causal_dataset_id:
            parts.append("Causal workbook did not yield a causal_dataset id.")
            diag = getattr(db_repo, "LAST_CAUSAL_PERSIST_DIAGNOSTIC", None)
            if diag:
                parts.append(diag)
        return {
            "success": False,
            "message": " ".join(parts),
            "dataset_id": None,
            "error_code": "integrity",
        }

    if causal_rows:
        n_causal_db = db_repo.count_causal_rows_for_causal_dataset(causal_dataset_id)
        if n_causal_db == 0:
            logger.warning(
                "Plant upload: causal_dataset id=%s has 0 causal_row after persist; re-ingesting",
                causal_dataset_id,
            )
            re_err = db_repo.reingest_causal_workbook_for_dataset_id(
                causal_dataset_id,
                causal_xlsx_bytes,
                causal_filename or "causal.xlsx",
            )
            if re_err:
                logger.warning("Causal reingest reported: %s", re_err)
            n_causal_db = db_repo.count_causal_rows_for_causal_dataset(causal_dataset_id)
        if n_causal_db == 0:
            msg_parts = [
                "Causal matrix was read from the file, but rows were not stored in "
                "causal_sheet / causal_row. Use a standard .xlsx with visible data rows on each sheet.",
            ]
            diag = getattr(db_repo, "LAST_CAUSAL_PERSIST_DIAGNOSTIC", None)
            if diag:
                msg_parts.append(diag)
            return {
                "success": False,
                "message": " ".join(msg_parts),
                "dataset_id": None,
                "error_code": "causal_persist",
            }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(insert_plant, (plant_name,))
                    dataset_id = int(cur.lastrowid)
                except PyMySQLIntegrityError:
                    cur.execute(
                        "SELECT dataset_id FROM plant_dataset WHERE plant_name = %s",
                        (plant_name,),
                    )
                    ex = cur.fetchone()
                    if not ex:
                        return {
                            "success": False,
                            "message": f"A plant with name '{plant_name}' already exists.",
                            "dataset_id": None,
                            "error_code": "duplicate_plant",
                        }
                    dataset_id = int(ex[0])

                cur.execute(
                    """
                    UPDATE plant_dataset
                    SET plant_name = %s,
                        timeseries_dataset_id = %s,
                        causal_dataset_id = %s,
                        causal_matrix_dataset_id = %s
                    WHERE dataset_id = %s
                    """,
                    (
                        plant_name,
                        int(ts_dataset_id),
                        int(causal_dataset_id),
                        int(causal_dataset_id),
                        dataset_id,
                    ),
                )

                ts_tuples: List[Tuple[Any, ...]] = [
                    (dataset_id, i, _j(row)) for i, row in enumerate(ts_rows)
                ]
                causal_tuples: List[Tuple[Any, ...]] = [
                    (dataset_id, sheet, ridx, _j(row)) for sheet, ridx, row in causal_rows
                ]

                if ts_tuples:
                    _executemany_chunked(cur, insert_ts, list(ts_tuples))
                if causal_tuples:
                    _executemany_chunked(cur, insert_causal, list(causal_tuples))

        return {
            "success": True,
            "message": f"Dataset saved for plant '{plant_name}' (plant_dataset_id={dataset_id}). "
            f"timeseries_dataset_id={int(ts_dataset_id)}, causal_dataset_id={int(causal_dataset_id)}. "
            f"Parsed rows -> time_series_data: {len(ts_rows)}, causal_data: {len(causal_tuples)}. "
            f"Files: {timeseries_filename!r}, {causal_filename!r}.",
            "dataset_id": dataset_id,
            "error_code": None,
        }
    except PyMySQLIntegrityError as e:
        logger.exception("plant upload integrity error: %s", e)
        return {
            "success": False,
            "message": "Database conflict while saving (duplicate or invalid reference). All changes were rolled back.",
            "dataset_id": None,
            "error_code": "integrity",
        }
    except Exception as e:
        logger.exception("plant upload failed: %s", e)
        return {
            "success": False,
            "message": f"Upload failed: {e}",
            "dataset_id": None,
            "error_code": "server",
        }


def seed_plant_yanpet_olf1_if_data_exists() -> Dict[str, Any]:
    """
    Insert plant_dataset (dataset_id=2, plant_name='Yanpet OLF1') only when
    both time_series_data and causal_data already contain rows for dataset_id=2,
    and plant_dataset does not already have dataset_id=2.

    Assumption: rows were loaded with dataset_id=2 by a DBA/migration; this
    links the human-readable plant name without re-uploading files.
    """
    if not is_configured():
        return {"success": False, "message": "DATABASE_URL not set.", "inserted": False}

    apply_schema_if_needed()

    # If child rows were loaded before the parent existed, InnoDB would normally reject them.
    # Legacy loads often use FOREIGN_KEY_CHECKS=0; mirror that for this one-time parent row.
    sql = """
    INSERT INTO plant_dataset (dataset_id, plant_name)
    SELECT 2, 'Yanpet OLF1'
    WHERE EXISTS (SELECT 1 FROM time_series_data WHERE dataset_id = 2 LIMIT 1)
      AND EXISTS (SELECT 1 FROM causal_data WHERE dataset_id = 2 LIMIT 1)
      AND NOT EXISTS (SELECT 1 FROM plant_dataset WHERE dataset_id = 2 LIMIT 1)
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET FOREIGN_KEY_CHECKS=0")
                try:
                    cur.execute(sql)
                    inserted = cur.rowcount > 0
                finally:
                    cur.execute("SET FOREIGN_KEY_CHECKS=1")
        if inserted:
            return {
                "success": True,
                "message": "Inserted plant_dataset row dataset_id=2, plant_name='Yanpet OLF1'.",
                "inserted": True,
            }
        return {
            "success": True,
            "message": "No insert performed: either dataset_id=2 already exists in plant_dataset, "
            "or missing rows in time_series_data / causal_data for dataset_id=2.",
            "inserted": False,
        }
    except PyMySQLIntegrityError as e:
        logger.warning("seed yanpet plant integrity: %s", e)
        return {
            "success": False,
            "message": "Could not insert plant row (duplicate dataset_id or plant_name).",
            "inserted": False,
        }
    except Exception as e:
        logger.exception("seed yanpet plant: %s", e)
        return {"success": False, "message": str(e), "inserted": False}


def upsert_plant_yanpet_dataset_2() -> Dict[str, Any]:
    """
    Insert or update plant_dataset row: dataset_id = 2, plant_name = 'Yanpet OLF1'.

    Idempotent via ON DUPLICATE KEY UPDATE on primary key (dataset_id).
    FOREIGN_KEY_CHECKS is disabled briefly so this can run when time_series_data /
    causal_data already reference dataset_id = 2 but the parent row was missing.

    Fails if another row already uses plant_name 'Yanpet OLF1' with a different dataset_id
    (unique constraint on plant_name).
    """
    if not is_configured():
        return {"success": False, "message": "DATABASE_URL not set.", "upserted": False}

    apply_schema_if_needed()

    insert_sql = """
    INSERT INTO plant_dataset (dataset_id, plant_name)
    VALUES (2, 'Yanpet OLF1')
    ON DUPLICATE KEY UPDATE plant_name = VALUES(plant_name)
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET FOREIGN_KEY_CHECKS=0")
                try:
                    cur.execute(insert_sql)
                    # rowcount: 1 = insert, 2 = update in MySQL for ON DUPLICATE KEY UPDATE
                    changed = cur.rowcount > 0
                finally:
                    cur.execute("SET FOREIGN_KEY_CHECKS=1")
        return {
            "success": True,
            "message": "plant_dataset row ensured: dataset_id=2, plant_name='Yanpet OLF1'.",
            "upserted": changed,
        }
    except PyMySQLIntegrityError as e:
        logger.warning("upsert yanpet plant integrity: %s", e)
        return {
            "success": False,
            "message": "Could not upsert: duplicate plant_name on a different dataset_id, or other constraint.",
            "upserted": False,
        }
    except Exception as e:
        logger.exception("upsert yanpet plant: %s", e)
        return {"success": False, "message": str(e), "upserted": False}
