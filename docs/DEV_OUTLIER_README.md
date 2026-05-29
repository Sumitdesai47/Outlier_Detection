# Dev Outlier Detection - Simple User Guide

This guide explains how to use the **Dev (Outlier detection)** tab in simple steps.

## What kind of file you need

Use an Excel file (`.xlsx`) with this format:

- First column: `Timestamp`
- Other columns: numeric tag values (temperature, pressure, flow, etc.)
- Each row: one time point

Example:

| Timestamp | Tag_A | Tag_B | Tag_C |
|---|---:|---:|---:|
| 2026-01-01 00:00:00 | 12.4 | 88.1 | 41.9 |
| 2026-01-01 01:00:00 | 12.7 | 87.9 | 42.3 |

## Step-by-step usage

1. Open **Dev (Outlier detection)** tab.
2. Click **Download Sample File (XLSX)** if you want a reference format.
3. Upload your Excel file.
4. Wait for tag list to load automatically.
5. In the tag table:
   - Keep **Critical** checked for tags you want in results.
   - (Optional) Enable **Plant** filter and set condition/value to remove shutdown-like rows.
   - Keep default threshold/signals unless you have a specific reason to tune.
6. Click **Process data**.
7. Review output tabs:
   - **Tag Analysis**: tag-wise status and anomaly count
   - **Graph & Correlation**: trend + related tags
   - **Event Details**: flagged timestamps with explanation

## How to read results quickly

- Start with **Total Outliers** KPI (high value means many strong anomaly events).
- In **Tag Analysis**, sort/search tags with highest anomaly count first.
- In **Event Details**, filter by date to inspect what happened at specific times.
- Use download options (Excel/CSV/PDF) for reporting.

## Common mistakes to avoid

- Missing `Timestamp` column
- Text values inside numeric tag columns
- Very small files with too few rows
- Duplicate/invalid timestamps

## Tips for better output quality

- Keep regular time intervals if possible
- Remove obvious bad data before upload
- Mark only meaningful process tags as **Critical**
- Use plant filters only when you are sure about shutdown logic
