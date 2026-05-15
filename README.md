# Outlier Detection

Flask dashboard for time-series outlier workflows; optional Streamlit Dev-only UI and CLI helpers live under `services/`.

---

## Run the web app

1. Install dependencies: `pip install -r requirements.txt`  
2. From the project root, start the server: `python app.py` (PowerShell: `Set-Location <project>; python app.py`).  
3. Open **http://127.0.0.1:5001** (or the host/port set by `FLASK_PORT` / `FLASK_RUN_HOST` in `.env`).

---

## Dev (Outlier detection) tab — how to use

**Word copy:** `docs/Dev_Outlier_Tab_User_Guide.docx` (regenerate with `python scripts/build_dev_outlier_docx.py`).  
Use the sidebar link **Dev (Outlier detection)** (`?tab=part15` on the home page). The same multi-signal engine as **Multi-signal consensus outlier**, with extra per-tag controls.

### Steps

1. **Upload workbook**  
   Choose an `.xlsx` file with timestamps and numeric tags (wide columns or long format).  
   The UI reads tag names from the sheet after you pick the file.

2. **Wait for the tag table**  
   After upload, a per-tag grid appears (critical tag, plant filter, threshold, eight signal engines, direction).  
   Enable **Process data** only after the table is built and any validation messages are resolved.

3. **Critical tag (Crit.)**  
   Check **Crit.** for tags you want in the focused results list and for whom threshold / engines / direction apply.  
   Unchecked tags are still in the file but are not driven by that row’s advanced settings in the same way.

4. **Threshold value**  
   For critical tags, enter a reference multiplier vs the preset robust-z scale (default shown in the table).  
   Leave default if you want the preset behaviour.

5. **Plant row filter (per tag)**  
   Enable **Plant**, pick operator and value: rows where `(tag operator value)` is **true** are **dropped** before detection (OR across tags with plant enabled).  
   Dropped rows are not used for limits, training, or plots.

6. **Signal engines (checkboxes)**  
   Uncheck an engine to skip it for that tag (skipped engines do not count as firing).  
   Defaults follow the multi-signal preset unless you change them.

7. **Direction**  
   Choose **Up**, **Down**, or **Both** so only excursions in that direction can fire the level-style checks for that tag.  
   **Both** keeps upward and downward sensitivity.

8. **Submit**  
   Click **Process data**; the app runs the pipeline and opens the results page.  
   Large files may take noticeable time; do not double-submit.

9. **Results — summary**  
   The run summary lists counts, preset parameters (S6/S7/S8 when enabled), and any filters applied.  
   Use it to confirm the run matches what you configured on the form.

10. **Results — plots and tables**  
    On Dev results, the detail grid lists **Strong Anomaly** rows only; pick a tag from the dropdown for charts.  
    **Anomaly explanation** is short plain language plus failed checks; **Reason** adds metrics and official engine names that fired.

11. **Download**  
    Use the Excel download control on the results page when you need the full export bundle for the session.  
    Session results expire if you leave the page too long or restart the server.

### Optional — same logic without the browser

- **CLI:** `python -m services.dev_outlier_detection_tab <input.xlsx> -o <output.xlsx>` with optional `--advanced-json` and `--critical-tags`.  
  Writes summary/detail-style sheets without loading the full Flask UI.

- **Streamlit:** `streamlit run streamlit_app.py` (requires `streamlit` installed).  
  Separate Dev-only flow with plant/MFI-DOL filters and previews; see `services/streamlit_dev_outlier_pipeline.py`.

### Configuration file (`.env`)

- **`FLASK_SECRET_KEY`**, **`FLASK_PORT`**, **`DATABASE_URL`** (only if you use DB-backed features like Live dashboard).  
  Wrong MySQL credentials do not block upload-only outlier tabs; fix `DATABASE_URL` for database features.
