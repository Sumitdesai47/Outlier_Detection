"""
Multimodel Outlier Detection — tab **part16**.

Same upload + tag configuration UX as Dev (part15), but S5 uses staged feature
selection + linear/nonlinear CV winner (see ``services.multimodel_outlier``).
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from services.dataset_upload_parse import validate_excel_filename
from services.dev_outlier_detection_tab import (
    Part15AdvancedOptions,
    collect_critical_tags_from_form,
    parse_part15_advanced_json,
)
from services.robust_consensus_outlier_workflow import MULTI_SIGNAL_PRESET, run_robust_consensus_outlier_ui

logger = logging.getLogger(__name__)

PART16_UPLOAD_FIELD = "part16_multimodel_xlsx"
FORM_PART16_TAG_CONFIG = "part16_tag_config"
FORM_PART16_ADVANCED_JSON = "part16_advanced_json"
FORM_CRITICAL_TAGS = "critical_tags"

RUN_PRESET_SUMMARY_LINE = (
    "Multimodel Outlier Detection — MULTI_SIGNAL_PRESET; S5 on every tag uses "
    "6-stage feature selection + ElasticNet/Ridge/Lasso or GBR/RF/SVR (CV RMSE winner)."
)


def _save_upload_to_temp_path(file_storage: Any, *, suffix: str = ".xlsx") -> str:
    tmpdir = tempfile.mkdtemp(prefix="multimodel_outlier_upload_")
    name = getattr(file_storage, "filename", None) or ""
    path = os.path.join(tmpdir, name if name.strip() else f"upload{suffix}")
    file_storage.save(path)
    return path


def run_multimodel_outlier_tab_pipeline(
    drift_xlsx_path: str,
    *,
    critical_tags: List[str],
    tag_config_used: bool,
    advanced: Part15AdvancedOptions,
) -> Dict[str, Any]:
    crit = list(critical_tags) if tag_config_used else None
    return run_robust_consensus_outlier_ui(
        drift_xlsx_path,
        shutdown_indicator_tags=None,
        critical_tags=crit,
        config=MULTI_SIGNAL_PRESET,
        plant_status_filter=advanced.plant_status_filter,
        plant_row_filters=advanced.plant_row_filters,
        per_tag_controls=advanced.per_tag_controls,
        extra_summary={"Run_Preset": RUN_PRESET_SUMMARY_LINE},
        use_multimodel_s5=True,
    )


def handle_part16_post_request(request: Any) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    drift_xlsx = request.files.get(PART16_UPLOAD_FIELD)
    if not drift_xlsx:
        return f"Missing file: {PART16_UPLOAD_FIELD}", None
    if not getattr(drift_xlsx, "filename", None):
        return "Please choose an Excel (.xlsx) file.", None
    try:
        validate_excel_filename(drift_xlsx.filename)
    except ValueError as e:
        return str(e), None

    critical_tags = collect_critical_tags_from_form(request.form)
    tag_config_used = request.form.get(FORM_PART16_TAG_CONFIG) == "1"
    raw_adv = (request.form.get(FORM_PART16_ADVANCED_JSON) or "").strip()
    advanced, adv_err = parse_part15_advanced_json(raw_adv)
    if adv_err:
        return adv_err, None

    try:
        drift_xlsx_path = _save_upload_to_temp_path(drift_xlsx, suffix=".xlsx")
        result = run_multimodel_outlier_tab_pipeline(
            drift_xlsx_path,
            critical_tags=critical_tags,
            tag_config_used=tag_config_used,
            advanced=advanced,
        )
    except Exception as e:
        logger.exception("Multimodel outlier upload failed: %s", e)
        hint = str(e).strip() if str(e) else type(e).__name__
        return (
            "Could not process the uploaded file for Multimodel Outlier Detection. "
            "Please verify the Excel format (time + numeric tags, wide or long). "
            f"Detail: {hint}",
            None,
        )
    return None, result
