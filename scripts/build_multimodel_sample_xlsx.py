"""Create multimodel sample XLSX with Instructions sheet. Run: python scripts/build_multimodel_sample_xlsx.py"""
from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    out = docs / "multimodel_outlier_sample_template.xlsx"
    src = docs / "dev_outlier_sample_template.xlsx"

    if src.is_file():
        shutil.copy2(src, out)
        df = pd.read_excel(out)
    else:
        # Minimal synthetic sample
        import numpy as np

        n = 48
        ts = pd.date_range("2026-01-01", periods=n, freq="h")
        rng = np.random.default_rng(42)
        df = pd.DataFrame(
            {
                "Timestamp": ts,
                "Compressor_Discharge_Pressure": 88.0 + rng.normal(0, 0.5, n),
                "Turbine_Inlet_Temperature": 540.0 + rng.normal(0, 2.0, n),
                "Generator_Load_MW": 120.0 + rng.normal(0, 3.0, n),
            }
        )

    instructions = pd.DataFrame(
        {
            "Topic": [
                "File format",
                "Timestamp column",
                "Tag columns",
                "Row rules",
                "Upload",
                "Critical tags",
                "Process",
            ],
            "What you need to do": [
                "Save as .xlsx with one sheet of time-series data (see Data sheet).",
                "First column named Timestamp; dates like 2026-01-01 00:00:00.",
                "Every other column = one tag; numbers only (no text in value cells).",
                "One row per time point; no blank rows in the middle.",
                "In the portal: Multimodel Outlier Detection → Choose file → wait for tag table.",
                "Check Crit. on tags you care about most; configure threshold if needed.",
                "Click Process data and wait for the results page (may take minutes).",
            ],
        }
    )

    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Data", index=False)
        instructions.to_excel(writer, sheet_name="Instructions", index=False)

    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
