"""Verify multimodel meta exists for all tags (not only critical)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402

SAMPLE = ROOT / "Turbine outlier (1).xlsx"


def main() -> int:
    client = app.test_client()
    with open(SAMPLE, "rb") as f:
        prev = client.post(
            "/api/part8/preview-tags",
            data={"file": (f, SAMPLE.name)},
            content_type="multipart/form-data",
        )
    tags = (prev.get_json() or {}).get("tags") or []
    crit = tags[:2]
    tag_config = {
        t: {
            "threshold": 3.75,
            "selected_engines": [
                "S1_GLOBAL",
                "S2_LOCAL",
                "S3_TUKEY",
                "S4_DIFF",
                "S5_PEER",
                "S6_LONG",
                "S7_TREND",
                "S8_EARLY",
            ],
            "direction": "both",
        }
        for t in crit
    }
    adv = json.dumps({"plant_row_filters": [], "tag_config": tag_config})
    with open(SAMPLE, "rb") as f:
        post = client.post(
            "/part16/multimodel-outlier-detection",
            data={
                "part16_multimodel_xlsx": (f, SAMPLE.name),
                "part16_tag_config": "1",
                "part16_advanced_json": adv,
                "critical_tags": crit,
            },
            content_type="multipart/form-data",
        )
    text = post.data.decode("utf-8", errors="ignore")
    m = re.search(
        r'<script[^>]*id="part4-config"[^>]*>(.*?)</script>',
        text,
        re.DOTALL,
    )
    if not m:
        print("FAIL: no part4-config")
        return 1
    cfg = json.loads(m.group(1).strip())
    meta = cfg.get("multimodelMetaByTag") or {}
    all_tags = cfg.get("allTags") or []
    non_crit = [t for t in all_tags if t not in crit]
    has = sum(1 for t in non_crit if t in meta)
    print(f"meta={len(meta)} allTags={len(all_tags)} non_crit_with_meta={has}/{len(non_crit)}")
    if has < len(non_crit):
        missing = [t for t in non_crit if t not in meta][:5]
        print("missing sample:", missing)
        return 1
    sample_tag = non_crit[0] if non_crit else all_tags[0]
    sm = meta.get(sample_tag) or {}
    print(
        f"sample={sample_tag!r} winner={sm.get('winner_model')} "
        f"candidates={len(sm.get('model_candidates') or [])} "
        f"x_variables={len(sm.get('x_variables') or [])}"
    )
    if not (sm.get("model_candidates") or []):
        print("FAIL: no model_candidates")
        return 1
    if not (sm.get("feature_selection") or []):
        print("FAIL: no feature_selection rows")
        return 1
    done = [c for c in (sm.get("model_candidates") or []) if c.get("status") in ("done", "evaluated")]
    if not done or done[0].get("cv_r2") is None:
        print("WARN: missing cv_r2 on candidates")
    if "All models" not in text and "CV RMSE (lower is better)" not in text:
        print("WARN: new models tab copy not in HTML (re-run may need browser refresh)")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
