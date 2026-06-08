"""Smoke test for Plant Analysis analyze API."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

boundary = "----PlantAnalysisBoundary"
file_path = Path(__file__).resolve().parent.parent / "outlier_data_filter_2.xlsx"
data = file_path.read_bytes()
config = json.dumps({"critical_tags": []})

body = (
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="plant_name"\r\n\r\n'
    "Plant 1\r\n"
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="subsystem"\r\n\r\n'
    "Furnace\r\n"
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="dataset_name"\r\n\r\n'
    "test.xlsx\r\n"
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="config_json"\r\n\r\n'
    f"{config}\r\n"
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="file"; filename="outlier_data_filter_2.xlsx"\r\n'
    "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
).encode() + data + f"\r\n--{boundary}--\r\n".encode()

req = urllib.request.Request(
    "http://127.0.0.1:5001/plant-analysis/api/analyze",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=120) as resp:
    payload = json.loads(resp.read().decode())
    print("status", resp.status)
    print("run_id", payload.get("run_id"))
    print("outliers", payload.get("summary", {}).get("total_outlier_points"))
