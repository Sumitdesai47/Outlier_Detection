"""
Dev (Outlier detection) — UI tab **part15**.

Everything specific to this tab lives here: form field names, advanced JSON parsing,
temp upload handling, preset/summary wiring, and a CLI entry point so you can run the
same pipeline without loading unrelated dashboard routes.

The statistical engine remains ``robust_consensus_outlier_workflow`` (MULTI_SIGNAL_PRESET +
``run_robust_consensus_outlier_ui``); this module is the tab’s orchestration layer only.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from services.dataset_upload_parse import validate_excel_filename
from services.robust_consensus_outlier_workflow import MULTI_SIGNAL_PRESET, run_robust_consensus_outlier_ui

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tab contract: field names and run metadata (keep in sync with templates/index.html)
# ---------------------------------------------------------------------------

PART15_UPLOAD_FIELD = "auto_testing_dev_multi_signal_xlsx"
FORM_PART15_TAG_CONFIG = "part15_tag_config"
FORM_PART15_ADVANCED_JSON = "part15_advanced_json"
FORM_CRITICAL_TAGS = "critical_tags"

RUN_PRESET_SUMMARY_LINE = (
    "Dev (Outlier detection) — MULTI_SIGNAL_PRESET + optional plant row filter + "
    "per-tag thresholds / engines / direction."
)


@dataclass(frozen=True)
class Part15AdvancedOptions:
    plant_status_filter: Optional[Dict[str, Any]] = None
    plant_row_filters: Optional[List[Dict[str, Any]]] = None
    per_tag_controls: Optional[Dict[str, Dict[str, Any]]] = None


def parse_part15_advanced_json(raw: str) -> Tuple[Part15AdvancedOptions, Optional[str]]:
    """
    Parse the hidden ``part15_advanced_json`` payload (plant filters + per-tag controls).

    Returns ``(options, error_message)``. On success ``error_message`` is None.
    """
    text = (raw or "").strip()
    if not text:
        return Part15AdvancedOptions(), None
    try:
        adv = json.loads(text)
    except json.JSONDecodeError:
        return Part15AdvancedOptions(), "Invalid advanced configuration JSON."
    if not isinstance(adv, dict):
        return Part15AdvancedOptions(), "Advanced configuration must be a JSON object."

    plant_status_filter: Optional[Dict[str, Any]] = None
    plant_row_filters: Optional[List[Dict[str, Any]]] = None
    per_tag_controls: Optional[Dict[str, Dict[str, Any]]] = None

    pr = adv.get("plant_row_filters")
    if isinstance(pr, list) and pr:
        tmp: List[Dict[str, Any]] = []
        for item in pr:
            if not isinstance(item, dict):
                continue
            st = str(item.get("status_tag") or item.get("tag") or "").strip()
            op = str(item.get("operator") or "").strip()
            if not st or not op:
                continue
            tmp.append({"status_tag": st, "operator": op, "value": item.get("value")})
        plant_row_filters = tmp or None

    if plant_row_filters is None:
        pf = adv.get("plant_status_filter")
        if isinstance(pf, dict) and pf.get("enabled"):
            plant_status_filter = {
                "enabled": True,
                "status_tag": str(pf.get("status_tag") or "").strip(),
                "operator": str(pf.get("operator") or "").strip(),
                "value": pf.get("value"),
            }

    tc = adv.get("tag_config")
    if isinstance(tc, dict) and tc:
        per_tag_controls = {
            str(k).strip(): v for k, v in tc.items() if str(k).strip() and isinstance(v, dict)
        }

    return (
        Part15AdvancedOptions(
            plant_status_filter=plant_status_filter,
            plant_row_filters=plant_row_filters,
            per_tag_controls=per_tag_controls,
        ),
        None,
    )


def _save_upload_to_temp_path(file_storage: Any, *, suffix: str = ".xlsx") -> str:
    """Persist an uploaded file object to a temp path (tab-local; avoids coupling to other upload helpers)."""
    tmpdir = tempfile.mkdtemp(prefix="dev_outlier_tab_upload_")
    name = getattr(file_storage, "filename", None) or ""
    path = os.path.join(tmpdir, name if name.strip() else f"upload{suffix}")
    file_storage.save(path)
    return path


def collect_critical_tags_from_form(form: Any) -> List[str]:
    raw = form.getlist(FORM_CRITICAL_TAGS) if hasattr(form, "getlist") else []
    return [str(x).strip() for x in raw if x and str(x).strip()]


def run_dev_outlier_tab_pipeline(
    drift_xlsx_path: str,
    *,
    critical_tags: Sequence[str],
    tag_config_used: bool,
    advanced: Part15AdvancedOptions,
) -> Dict[str, Any]:
    """
    Execute the Dev tab detection on a workbook already on disk.

    Returns the same dict as ``run_robust_consensus_outlier_ui`` (including ``df_for_script``
    and ``out_df``).
    """
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
    )


def handle_part15_post_request(request: Any) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Full Dev-tab POST handling: validate upload, parse advanced JSON, run pipeline.

    Returns ``(error_message, result_dict)``. On success ``error_message`` is None and
    ``result_dict`` is the workflow output (caller may ``pop`` session-bound frames).
    """
    drift_xlsx = request.files.get(PART15_UPLOAD_FIELD)
    if not drift_xlsx:
        return f"Missing file: {PART15_UPLOAD_FIELD}", None
    if not getattr(drift_xlsx, "filename", None):
        return "Please choose an Excel (.xlsx) file.", None
    try:
        validate_excel_filename(drift_xlsx.filename)
    except ValueError as e:
        return str(e), None

    critical_tags = collect_critical_tags_from_form(request.form)
    tag_config_used = request.form.get(FORM_PART15_TAG_CONFIG) == "1"
    raw_adv = (request.form.get(FORM_PART15_ADVANCED_JSON) or "").strip()
    advanced, adv_err = parse_part15_advanced_json(raw_adv)
    if adv_err:
        return adv_err, None

    try:
        drift_xlsx_path = _save_upload_to_temp_path(drift_xlsx, suffix=".xlsx")
        result = run_dev_outlier_tab_pipeline(
            drift_xlsx_path,
            critical_tags=critical_tags,
            tag_config_used=tag_config_used,
            advanced=advanced,
        )
    except Exception as e:
        logger.exception("Dev (Outlier detection) upload failed: %s", e)
        hint = str(e).strip() if str(e) else type(e).__name__
        return (
            "Could not process the uploaded file for Dev (Outlier detection). "
            "Please verify the Excel format (time + numeric tags, wide or long). "
            f"Detail: {hint}",
            None,
        )
    return None, result


def write_part15_cli_excel(output_path: str, result: Dict[str, Any]) -> None:
    """Write Tag_Summary / Detail_Rows / Monthly_Pages sheets (same layout as session download)."""
    payload = {
        "tag_summaries": result.get("tag_summaries") or [],
        "details_by_tag": result.get("details_by_tag") or {},
        "monthly_pages_by_tag": result.get("monthly_pages_by_tag") or {},
    }
    tag_summaries = payload["tag_summaries"]
    details_by_tag = payload["details_by_tag"]
    monthly_pages_by_tag = payload["monthly_pages_by_tag"]

    summary_df = pd.DataFrame(tag_summaries)
    detail_rows: List[Dict[str, Any]] = []
    for tag, rows in (details_by_tag or {}).items():
        for r in rows or []:
            one: Dict[str, Any] = {"Tag": tag}
            one.update(r)
            detail_rows.append(one)
    details_df = pd.DataFrame(detail_rows)

    monthly_rows: List[Dict[str, Any]] = []
    for tag, pages in (monthly_pages_by_tag or {}).items():
        for p in pages or []:
            month = p.get("month", "")
            for r in p.get("rows", []) or []:
                one = {"Tag": tag, "Month": month}
                one.update(r)
                monthly_rows.append(one)
    monthly_df = pd.DataFrame(monthly_rows)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Tag_Summary")
        details_df.to_excel(writer, index=False, sheet_name="Detail_Rows")
        monthly_df.to_excel(writer, index=False, sheet_name="Monthly_Pages")


def _load_advanced_from_cli_arg(raw: str) -> Tuple[Part15AdvancedOptions, Optional[str]]:
    text = (raw or "").strip()
    if not text:
        return Part15AdvancedOptions(), None
    if text.startswith("@"):
        path = text[1:].strip()
        with open(path, encoding="utf-8") as f:
            text = f.read()
    return parse_part15_advanced_json(text)


def _cli(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Dev (Outlier detection) tab: run MULTI_SIGNAL pipeline on an .xlsx without the web UI.",
    )
    p.add_argument("xlsx", help="Path to input .xlsx (wide or long time series).")
    p.add_argument("-o", "--output", required=True, help="Output .xlsx path (Tag_Summary / Detail_Rows / Monthly_Pages).")
    p.add_argument(
        "--advanced-json",
        default="",
        help='Advanced JSON string, or @path to a UTF-8 file (plant_row_filters / plant_status_filter / tag_config).',
    )
    p.add_argument(
        "--critical-tags",
        default="",
        help="Comma-separated tag names to treat as configured critical tags (optional).",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    advanced, err = _load_advanced_from_cli_arg(args.advanced_json)
    if err:
        print(err, flush=True)
        return 1

    crit = [t.strip() for t in str(args.critical_tags).split(",") if t.strip()]
    tag_config_used = bool(crit)

    try:
        result = run_dev_outlier_tab_pipeline(
            args.xlsx,
            critical_tags=crit,
            tag_config_used=tag_config_used,
            advanced=advanced,
        )
        write_part15_cli_excel(args.output, result)
    except Exception as e:
        logger.exception("CLI Dev outlier run failed: %s", e)
        print(str(e).strip() or type(e).__name__, flush=True)
        return 1
    print(f"Wrote {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
