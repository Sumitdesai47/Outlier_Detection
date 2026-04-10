"""
Transactional insert: plant_dataset + time_series_data + causal_data.

All inserts run in one DB transaction; any failure rolls back.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

from pymysql.err import IntegrityError as PyMySQLIntegrityError

from .db_config import get_connection, is_configured
from .db_repository import _executemany_chunked, apply_schema_if_needed
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
    Validate plant name, parse both workbooks, insert plant then child rows.

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
    """
    insert_causal = """
        INSERT INTO causal_data (dataset_id, sheet_name, row_index, row_data)
        VALUES (%s, %s, %s, %s)
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(insert_plant, (plant_name,))
                    dataset_id = int(cur.lastrowid)
                except PyMySQLIntegrityError:
                    return {
                        "success": False,
                        "message": f"A plant with name '{plant_name}' already exists. Choose a different name.",
                        "dataset_id": None,
                        "error_code": "duplicate_plant",
                    }

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
            "message": f"Dataset saved for plant '{plant_name}' (dataset_id={dataset_id}). "
            f"Time series rows: {len(ts_rows)}, causal rows: {len(causal_tuples)}. "
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
