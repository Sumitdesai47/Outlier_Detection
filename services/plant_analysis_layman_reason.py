"""Plain-language outlier explanations for Plant Analysis result pages."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

ENGINE_FIRE_COLUMNS: List[tuple[str, str]] = [
    ("Fire_S1_GLOBAL", "Overall level check"),
    ("Fire_S2_LOCAL", "Recent pattern check"),
    ("Fire_S3_TUKEY", "Normal operating range (fence) check"),
    ("Fire_S4_DIFF", "Sudden jump / step-change check"),
    ("Fire_S5_PEER", "Comparison with similar tags (peer check)"),
    ("Fire_S6_LONG", "Long-term level shift check"),
    ("Fire_S7_TREND", "Short vs long trend check"),
    ("Fire_S8_EARLY", "Early baseline period check"),
]

ENGINE_PLAIN_WHY: Dict[str, str] = {
    "Fire_S1_GLOBAL": "The value was unusually high or low compared with the full history for this tag.",
    "Fire_S2_LOCAL": "The value did not match the recent day-to-day pattern for this tag.",
    "Fire_S3_TUKEY": "The value sat outside the usual operating band (fence limits).",
    "Fire_S4_DIFF": "The value changed sharply compared with the previous reading (possible jump or step).",
    "Fire_S5_PEER": "Similar tags in the plant looked normal, but this tag did not follow them.",
    "Fire_S6_LONG": "The value shifted away from the longer-term typical level.",
    "Fire_S7_TREND": "Recent readings diverged from the longer-term trend.",
    "Fire_S8_EARLY": "The value did not match the expected pattern from the early baseline period.",
}

_FINAL_CLASS_PLAIN: Dict[str, str] = {
    "Strong Anomaly": "a strong unusual reading (clear spike or dip)",
    "Drift": "a sudden jump or sustained step-change in the signal",
    "Contextual Anomaly": "an unusual reading in context of nearby operation",
    "Drift + Anomaly": "both a shift and an unusual level at the same time",
    "Anomaly": "an unusual reading",
    "Warning": "a pattern worth watching that may develop into a larger issue",
}

_DIRECTION_PLAIN: Dict[str, str] = {
    "High": "higher than expected",
    "Low": "lower than expected",
    "Up": "moved upward",
    "Down": "moved downward",
}


def _fmt_num(value: Any, digits: int = 4) -> Optional[str]:
    try:
        if value is None:
            return None
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return None


def _section(title: str, lines: List[str]) -> str:
    body = [ln.strip() for ln in lines if str(ln).strip()]
    if not body:
        return ""
    return f"[{title}]\n" + "\n".join(f"• {ln}" for ln in body)


def extract_failed_engines(row: Dict[str, Any]) -> List[Dict[str, str]]:
    """Structured list of engines that failed on this row (for UI tooltips)."""
    out: List[Dict[str, str]] = []
    for col, label in ENGINE_FIRE_COLUMNS:
        if bool(row.get(col)):
            engine_id = col.replace("Fire_", "")
            out.append(
                {
                    "id": engine_id,
                    "label": label,
                    "detail": ENGINE_PLAIN_WHY.get(
                        col, f"The {label.lower()} raised a concern."
                    ),
                }
            )
    if bool(row.get("Fire_S8_EARLY_STRONG")):
        out.append(
            {
                "id": "S8_EARLY_STRONG",
                "label": "Early baseline (strong)",
                "detail": "The reading strongly disagreed with the early reference period.",
            }
        )
    if bool(row.get("Outside_Fence")) and not any(
        bool(row.get(col)) for col, _ in ENGINE_FIRE_COLUMNS if col == "Fire_S3_TUKEY"
    ):
        out.append(
            {
                "id": "OUTSIDE_FENCE",
                "label": "Operating range",
                "detail": "The value fell outside the usual upper/lower fence limits.",
            }
        )
    return out


def _failed_engine_explanations(row: Dict[str, Any]) -> List[str]:
    return [f"{e['label']}: {e['detail']}" for e in extract_failed_engines(row)]


def build_simple_reason_summary(
    *,
    tag: str,
    final_class: str,
    s5_fired: bool,
    row: Optional[Dict[str, Any]] = None,
    actual: Any = None,
    predicted: Any = None,
) -> str:
    """One short sentence for operators — no scores, z-values, or engine codes."""
    _ = (tag, final_class)
    actual_s = _fmt_num(actual, 3)
    predicted_s = _fmt_num(predicted, 3)
    prefix = ""
    if actual_s and predicted_s:
        prefix = f"Measured value was {actual_s}; the model expected about {predicted_s}. "
    elif actual_s:
        prefix = f"Measured value was {actual_s}. "

    failed = extract_failed_engines(row or {})
    cause_map = {
        "S4_DIFF": "the reading changed suddenly compared with the previous value",
        "S3_TUKEY": "the value was outside its normal operating band",
        "OUTSIDE_FENCE": "the value was outside its normal operating band",
        "S1_GLOBAL": "the value was much higher or lower than this tag normally runs",
        "S2_LOCAL": "the value did not match the recent pattern for this tag",
        "S6_LONG": "the tag shifted away from its longer-term normal level",
        "S7_TREND": "the recent trend moved away from the longer-term trend",
        "S8_EARLY": "the value did not match the early baseline pattern",
        "S8_EARLY_STRONG": "the value did not match the early baseline pattern",
        "S5_PEER": "similar tags did not support this tag's behavior",
    }
    causes: List[str] = []
    for engine in failed:
        cause = cause_map.get(str(engine.get("id") or ""))
        if cause and cause not in causes:
            causes.append(cause)
    if causes:
        if len(causes) == 1:
            cause_text = causes[0]
        elif len(causes) == 2:
            cause_text = f"{causes[0]} and {causes[1]}"
        else:
            cause_text = f"{causes[0]}, {causes[1]}, and {causes[2]}"
        prefix += f"The system flagged this point because {cause_text}. "

    if s5_fired:
        return (
            prefix
            + "Related tags did not show the same behavior, so this may be a sensor, "
            "transmitter, or local control issue for this tag."
        )
    return (
        prefix
        + "Related tags moved in a similar way, so this is more likely a wider process "
        "change than one bad tag."
    )


def build_layman_outlier_reason(
    *,
    tag: str,
    row: Dict[str, Any],
    s5_fired: bool,
    final_class: str,
    actual: Any,
    predicted: Any,
    lower: Optional[float],
    upper: Optional[float],
    related: List[str],
    observed_at: Optional[str] = None,
) -> str:
    """Build a detailed, non-technical explanation for operators and plant staff."""
    fc = str(final_class or "Unusual reading").strip()
    fc_plain = _FINAL_CLASS_PLAIN.get(fc, fc.replace("_", " ").lower())
    direction = str(row.get("Direction") or "").strip()
    dir_plain = _DIRECTION_PLAIN.get(direction, direction.lower() if direction else "")

    sections: List[str] = []

    summary_bits = [f"Tag «{tag}» was flagged because it showed {fc_plain}."]
    if observed_at:
        summary_bits.append(f"Time of reading: {observed_at}.")
    sections.append(_section("What we found", summary_bits))

    reading_lines: List[str] = []
    actual_s = _fmt_num(actual)
    predicted_s = _fmt_num(predicted)
    if actual_s is not None:
        reading_lines.append(f"Measured value: {actual_s}.")
    if predicted_s is not None:
        reading_lines.append(f"Expected value (model): about {predicted_s}.")
    if actual_s is not None and predicted_s is not None:
        try:
            delta = abs(float(actual) - float(predicted))
            reading_lines.append(
                f"The reading was off by about {delta:.4f} from what the model expected."
            )
        except (TypeError, ValueError):
            pass
    if dir_plain:
        reading_lines.append(f"Direction: the signal was {dir_plain}.")
    lo_s = _fmt_num(lower)
    hi_s = _fmt_num(upper)
    if lo_s is not None or hi_s is not None:
        if lo_s is not None and hi_s is not None:
            reading_lines.append(f"Normal operating band for this tag: {lo_s} to {hi_s}.")
        elif lo_s is not None:
            reading_lines.append(f"Lower operating limit: {lo_s}.")
        elif hi_s is not None:
            reading_lines.append(f"Upper operating limit: {hi_s}.")
    if actual_s is not None and lo_s is not None and hi_s is not None:
        try:
            v = float(actual)
            if v < float(lower):
                reading_lines.append("The measured value is below the normal lower band.")
            elif v > float(upper):
                reading_lines.append("The measured value is above the normal upper band.")
        except (TypeError, ValueError):
            pass
    sections.append(_section("What the reading showed", reading_lines))

    failed = _failed_engine_explanations(row)
    sig = row.get("Signals_Fired")
    if failed:
        check_intro = (
            f"{len(failed)} independent statistical check(s) agreed this point was not normal"
        )
        if sig is not None:
            try:
                check_intro += f" (total signals fired: {int(sig)})"
            except (TypeError, ValueError):
                pass
        check_intro += ":"
        why_lines = [check_intro, *failed]
    else:
        why_lines = [
            "Several statistical checks were combined to detect this issue.",
            "The detailed engine flags were not stored for this row; re-run analysis for the fullest breakdown.",
        ]
        raw = str(row.get("Reason") or "").strip()
        if raw:
            why_lines.append(f"Technical note: {raw}")
    sections.append(_section("Why the system flagged this", why_lines))

    if s5_fired:
        issue_lines = [
            "This is a TAG ISSUE.",
            "The peer comparison check (S5) failed — this tag moved differently from similar tags at the same time.",
            "That usually points to a sensor, transmitter, or local control issue on this tag rather than a whole-plant upset.",
        ]
        if related:
            issue_lines.append(
                "Tags used for comparison: " + ", ".join(related[:5]) + "."
            )
        action_lines = [
            "Check whether this instrument or control loop is reading correctly.",
            "Compare with field readings or a redundant sensor if available.",
            "Review recent maintenance, calibration, or wiring on this tag.",
        ]
    else:
        issue_lines = [
            "This is a PROCESS ISSUE.",
            "The peer comparison check (S5) passed — similar tags moved in a related way.",
            "That usually means a wider plant or process change affected this tag together with others.",
        ]
        if related:
            issue_lines.append(
                "Related tags that moved together: " + ", ".join(related[:5]) + "."
            )
        action_lines = [
            "Review upstream process conditions (feeds, temperatures, pressures, flows).",
            "Check operating setpoints and recent operator or DCS changes.",
            "Look at the related tags listed above as a group rather than this tag alone.",
        ]
    sections.append(_section("Tag issue or process issue?", issue_lines))
    sections.append(_section("What you can do next", action_lines))

    return "\n\n".join(s for s in sections if s.strip())
