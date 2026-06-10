"""Optional MySQL sync for Plant Analysis live uploads (same tables as Live outlier data upload)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from services.db_config import is_configured
from services.live_outlier_analysis_persist import persist_v5_bundle_to_db
from services.live_outlier_excel_upload import _observation_tuples_from_wide
from services.plant_analysis_live_outlier_runner import _load_wide_like_live_excel_upload

logger = logging.getLogger(__name__)

_CHUNK = 2000


def sync_live_upload_to_mysql(
    *,
    plant_name: str,
    subsystem: str,
    dataset_name: str,
    file_path: str,
    original_filename: str,
    bundle: Dict[str, Any],
) -> Optional[int]:
    """
    Persist the same V5 bundle to MySQL ``live_outlier_excel_*`` tables when DATABASE_URL is set.
    Returns MySQL dataset_id or None if skipped / unavailable.
    """
    if not is_configured():
        return None

    display_name = " · ".join(
        part.strip()
        for part in (plant_name, subsystem, dataset_name)
        if part and str(part).strip()
    )
    if not display_name:
        display_name = dataset_name or original_filename or "upload"

    try:
        from services import db_repository as db_repo
        from services.db_config import get_connection

        db_repo.apply_schema_if_needed()
        wide = _load_wide_like_live_excel_upload(file_path)

        obs_sql = """
            INSERT INTO live_outlier_excel_observation
                (dataset_id, row_index, observed_at, observed_at_raw, tag_name, value)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                observed_at = VALUES(observed_at),
                observed_at_raw = VALUES(observed_at_raw),
                value = VALUES(value)
        """

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO live_outlier_excel_dataset (dataset_name, original_filename)
                    VALUES (%s, %s)
                    """,
                    (display_name[:512], (original_filename or "upload.xlsx").strip()[:512]),
                )
                dataset_id = int(cur.lastrowid)
                tuples = _observation_tuples_from_wide(wide, dataset_id)
                for i in range(0, len(tuples), _CHUNK):
                    cur.executemany(obs_sql, tuples[i : i + _CHUNK])
            conn.commit()

        persist_v5_bundle_to_db(dataset_id, bundle)
        logger.info(
            "live upload synced to MySQL dataset_id=%s name=%r",
            dataset_id,
            display_name,
        )
        return dataset_id
    except Exception as exc:
        logger.warning("MySQL sync skipped for live upload: %s", exc)
        return None
