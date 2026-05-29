"""Export bundles for Multi-Signal / Dev consensus results (Excel, CSV, printable report)."""
from __future__ import annotations

import io
import zipfile
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from .consensus_results_view import (
    build_executive_summary,
    build_tag_analysis_rows,
    build_tag_insights,
)


def _detail_rows(details_by_tag: Dict[str, List[Dict[str, Any]]], tags: Optional[Sequence[str]] = None):
    out: List[Dict[str, Any]] = []
    keys = tags if tags else list(details_by_tag.keys())
    for tag in keys:
        for r in details_by_tag.get(str(tag)) or []:
            row = {"Tag": str(tag)}
            row.update(r)
            out.append(row)
    return out


def _correlation_rows(x_variables_by_tag: Dict[str, List[Dict[str, Any]]], tags: Optional[Sequence[str]] = None):
    out: List[Dict[str, Any]] = []
    keys = tags if tags else list(x_variables_by_tag.keys())
    for tag in keys:
        for p in x_variables_by_tag.get(str(tag)) or []:
            out.append(
                {
                    "Tag": str(tag),
                    "Correlated_Tag": p.get("tag"),
                    "Correlation": p.get("corr"),
                    "Cluster_ID": p.get("group_id"),
                    "Model_Importance": p.get("model_importance"),
                    "Mutual_Information": p.get("mutual_information"),
                }
            )
    return out


def _insights_rows(
    all_tags: Sequence[str],
    details_by_tag: Dict[str, List[Dict[str, Any]]],
    x_variables_by_tag: Dict[str, List[Dict[str, Any]]],
    tags: Optional[Sequence[str]] = None,
):
    out: List[Dict[str, Any]] = []
    keys = tags if tags else all_tags
    for tag in keys:
        ins = build_tag_insights(str(tag), details_by_tag, x_variables_by_tag)
        out.append(
            {
                "Tag": ins["tag"],
                "Flagged_Points": ins["flagged_count"],
                "Summary": ins["summary"],
                "Patterns": " | ".join(ins["patterns"]),
                "Contributing_Tags": " | ".join(ins["contributing_tags"]),
                "Sample_Explanations": " | ".join(ins["explanations"]),
            }
        )
    return out


def build_export_xlsx(payload: Dict[str, Any], tags: Optional[Sequence[str]] = None) -> bytes:
    summary_raw = payload.get("summary") or {}
    tag_summaries = payload.get("tag_summaries") or []
    all_plot_tags = payload.get("all_plot_tags") or []
    details_by_tag = payload.get("details_by_tag") or {}
    x_variables_by_tag = payload.get("x_variables_by_tag") or {}
    drift_points_by_tag = payload.get("drift_points_by_tag") or {}
    sudden_jumps_by_tag = payload.get("sudden_jumps_by_tag") or {}

    executive = build_executive_summary(
        summary_raw,
        tag_summaries,
        all_plot_tags,
        df_for_script=payload.get("df_for_script"),
        details_by_tag=details_by_tag,
        drift_points_by_tag=drift_points_by_tag,
    )
    tag_analysis = build_tag_analysis_rows(
        all_plot_tags,
        tag_summaries,
        details_by_tag,
        x_variables_by_tag,
        drift_points_by_tag,
        sudden_jumps_by_tag,
    )
    if tags:
        tag_set = {str(t) for t in tags}
        tag_analysis = [r for r in tag_analysis if r["tag"] in tag_set]

    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        pd.DataFrame([executive]).to_excel(writer, index=False, sheet_name="Executive_Summary")
        pd.DataFrame(tag_analysis).to_excel(writer, index=False, sheet_name="Tag_Analysis")
        pd.DataFrame(_detail_rows(details_by_tag, tags)).to_excel(writer, index=False, sheet_name="Events")
        pd.DataFrame(_correlation_rows(x_variables_by_tag, tags)).to_excel(
            writer, index=False, sheet_name="Correlation"
        )
        pd.DataFrame(_insights_rows(all_plot_tags, details_by_tag, x_variables_by_tag, tags)).to_excel(
            writer, index=False, sheet_name="Insights"
        )
    bio.seek(0)
    return bio.getvalue()


def build_export_csv_zip(payload: Dict[str, Any], tags: Optional[Sequence[str]] = None) -> bytes:
    summary_raw = payload.get("summary") or {}
    tag_summaries = payload.get("tag_summaries") or []
    all_plot_tags = payload.get("all_plot_tags") or []
    details_by_tag = payload.get("details_by_tag") or {}
    x_variables_by_tag = payload.get("x_variables_by_tag") or {}
    drift_points_by_tag = payload.get("drift_points_by_tag") or {}
    sudden_jumps_by_tag = payload.get("sudden_jumps_by_tag") or {}

    executive = build_executive_summary(
        summary_raw,
        tag_summaries,
        all_plot_tags,
        df_for_script=payload.get("df_for_script"),
        details_by_tag=details_by_tag,
        drift_points_by_tag=drift_points_by_tag,
    )
    tag_analysis = build_tag_analysis_rows(
        all_plot_tags,
        tag_summaries,
        details_by_tag,
        x_variables_by_tag,
        drift_points_by_tag,
        sudden_jumps_by_tag,
    )
    if tags:
        tag_set = {str(t) for t in tags}
        tag_analysis = [r for r in tag_analysis if r["tag"] in tag_set]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("executive_summary.csv", pd.DataFrame([executive]).to_csv(index=False))
        zf.writestr("tag_analysis.csv", pd.DataFrame(tag_analysis).to_csv(index=False))
        zf.writestr("events.csv", pd.DataFrame(_detail_rows(details_by_tag, tags)).to_csv(index=False))
        zf.writestr("correlation.csv", pd.DataFrame(_correlation_rows(x_variables_by_tag, tags)).to_csv(index=False))
        zf.writestr(
            "insights.csv",
            pd.DataFrame(_insights_rows(all_plot_tags, details_by_tag, x_variables_by_tag, tags)).to_csv(
                index=False
            ),
        )
    buf.seek(0)
    return buf.getvalue()


def build_export_pdf_html(payload: Dict[str, Any], tags: Optional[Sequence[str]] = None) -> str:
    """Print-friendly HTML report (Save as PDF from browser)."""
    summary_raw = payload.get("summary") or {}
    tag_summaries = payload.get("tag_summaries") or []
    all_plot_tags = payload.get("all_plot_tags") or []
    details_by_tag = payload.get("details_by_tag") or {}
    x_variables_by_tag = payload.get("x_variables_by_tag") or {}
    drift_points_by_tag = payload.get("drift_points_by_tag") or {}
    sudden_jumps_by_tag = payload.get("sudden_jumps_by_tag") or {}

    executive = build_executive_summary(
        summary_raw,
        tag_summaries,
        all_plot_tags,
        df_for_script=payload.get("df_for_script"),
        details_by_tag=details_by_tag,
        drift_points_by_tag=drift_points_by_tag,
    )
    tag_analysis = build_tag_analysis_rows(
        all_plot_tags,
        tag_summaries,
        details_by_tag,
        x_variables_by_tag,
        drift_points_by_tag,
        sudden_jumps_by_tag,
    )
    if tags:
        tag_set = {str(t) for t in tags}
        tag_analysis = [r for r in tag_analysis if r["tag"] in tag_set]

    insights = _insights_rows(all_plot_tags, details_by_tag, x_variables_by_tag, tags)

    def _table(rows: List[Dict[str, Any]], limit: int = 25) -> str:
        if not rows:
            return "<p>No data.</p>"
        cols = list(rows[0].keys())
        head = "".join(f"<th>{c}</th>" for c in cols)
        body = ""
        for row in rows[:limit]:
            body += "<tr>" + "".join(f"<td>{row.get(c, '')}</td>" for c in cols) + "</tr>"
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Consensus Results Report</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #111; }}
h1,h2 {{ color: #0f172a; }}
.kpi {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 16px 0; }}
.card {{ border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; }}
.card .v {{ font-size: 22px; font-weight: 700; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin: 12px 0; }}
th, td {{ border: 1px solid #e2e8f0; padding: 6px 8px; text-align: left; }}
th {{ background: #f8fafc; }}
@media print {{ body {{ margin: 12px; }} }}
</style></head><body>
<h1>Multi-Signal Consensus — Results Report</h1>
<div class="kpi">
  <div class="card"><div>Tags Analyzed</div><div class="v">{executive['total_tags_analyzed']}</div></div>
  <div class="card"><div>Strong Anomalies</div><div class="v">{executive['total_strong_outliers']}</div></div>
  <div class="card"><div>Analysis Period</div><div class="v">{executive['analysis_period']}</div></div>
  <div class="card"><div>Data Quality</div><div class="v">{executive['data_quality_score']}%</div></div>
  <div class="card"><div>Health</div><div class="v">{executive['health_status']}</div></div>
</div>
<h2>Tag Analysis</h2>
{_table(tag_analysis)}
<h2>Root Cause Insights</h2>
{_table(insights, 15)}
<p class="muted">Generated by Multi-Signal Consensus Outlier Detection dashboard.</p>
</body></html>"""
