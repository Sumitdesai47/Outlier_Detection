"""Smoke test part16 multimodel tab."""
from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402

SAMPLE = ROOT / "Turbine outlier (1).xlsx"
if not SAMPLE.is_file():
    SAMPLE = ROOT / "docs" / "dev_outlier_sample_template.xlsx"


def main() -> int:
    client = app.test_client()
    r = client.get("/?tab=part16")
    print("GET part16:", r.status_code, "len=", len(r.data))
    if b"part16OutlierForm" not in r.data:
        print("FAIL: part16 form missing")
        return 1

    if not SAMPLE.is_file():
        print("SKIP POST: no sample xlsx")
        return 0

    # Preview tags
    with open(SAMPLE, "rb") as f:
        prev = client.post(
            "/api/part8/preview-tags",
            data={"file": (f, SAMPLE.name)},
            content_type="multipart/form-data",
        )
    print("preview-tags:", prev.status_code)
    tags = []
    if prev.is_json:
        body = prev.get_json()
        tags = (body or {}).get("tags") or []
    if not tags:
        print("WARN: no tags from preview")
        return 1
    print("tags:", len(tags), tags[:3])

    crit = tags[: min(2, len(tags))]
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
    print("POST part16:", post.status_code, "len=", len(post.data))
    if post.status_code != 200:
        print(post.data[:2000].decode("utf-8", errors="replace"))
        return 1
    if b"consensus-enterprise" not in post.data:
        print("FAIL: enterprise results missing")
        return 1
    if b"multimodel_s5" in post.data or b"Multimodel" in post.data:
        print("OK: multimodel results page rendered")
    if b"part4-config" in post.data:
        print("OK: part4-config present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
