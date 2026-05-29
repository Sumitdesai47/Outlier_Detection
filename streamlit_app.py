"""
Streamlit entry: **Dev Outlier Detection** only.

Other outlier modes (generic outlier tab, data model, robust consensus) are not exposed
here — this matches the requirement to hide those tabs in this standalone dashboard.

Run from project root::

    streamlit run streamlit_app.py

All detection/filter orchestration lives in ``services/streamlit_dev_outlier_pipeline.py``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from services.robust_consensus_outlier_workflow import MULTI_SIGNAL_PRESET
from services.rolling_outlier_detection_service import run_rolling_outlier_detection
from services.rolling_outlier_sqlite import insert_results, list_runs, load_run_results
from services.streamlit_dev_outlier_pipeline import (
    ENGINE_OPTIONS_ORDERED,
    apply_additional_filters,
    apply_plant_status_filter,
    load_uploaded_streamlit_file,
    run_multi_signal_outlier_detection,
    validate_timestamp_column,
)

OPS = [">", ">=", "<", "<=", "==", "!="]
DIR_OPTIONS = ["Both", "Up only", "Down only"]


def _safe_key(tag: str, field: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in str(tag))[:48]
    return f"{field}__{safe}"


def _init_session() -> None:
    if "wide_df" not in st.session_state:
        st.session_state.wide_df = None
    if "load_error" not in st.session_state:
        st.session_state.load_error = None
    if "result_bundle" not in st.session_state:
        st.session_state.result_bundle = None


def _numeric_tag_columns(df: pd.DataFrame) -> List[str]:
    if df is None or df.empty:
        return []
    cols = [c for c in df.columns if c != "Timestamp"]
    out: List[str] = []
    for c in cols:
        if pd.api.types.is_numeric_dtype(df[c]) or df[c].dtype == object:
            s = pd.to_numeric(df[c], errors="coerce")
            if s.notna().sum() >= 5:
                out.append(str(c))
    return sorted(out)


def _render_rolling_outlier_tab() -> None:
    st.title("Rolling Outlier Detection")
    st.markdown(
        "Upload a dataset and run day-by-day/rolling outlier detection. "
        "Rows 1-30 are baseline; processing starts from row 31."
    )

    up = st.file_uploader("Upload dataset (.xlsx)", type=["xlsx"], key="rolling_upload")
    if up is None:
        st.info("Upload a dataset to run rolling outlier detection.")
    else:
        df, err = load_uploaded_streamlit_file(up)
        if err:
            st.error(err)
        else:
            st.success(f"Loaded **{len(df):,}** rows × **{len(df.columns)}** columns.")
            mode = st.selectbox(
                "Baseline mode",
                options=[("rolling", "Rolling previous 30 rows"), ("expanding", "All previous rows")],
                format_func=lambda x: x[1],
                key="rolling_mode",
            )
            c1, c2 = st.columns([1, 2])
            with c1:
                window = st.number_input("Window size", min_value=30, max_value=500, value=30, step=1)
            with c2:
                st.caption("For rolling mode, previous N rows are used. Expanding mode uses all prior rows.")

            if st.button("Run rolling outlier detection", type="primary"):
                try:
                    with st.spinner("Running rolling detection (Dev logic + SQLite persistence)..."):
                        result = run_rolling_outlier_detection(
                            df,
                            dataset_name=getattr(up, "name", "uploaded_dataset.xlsx"),
                            window_size=int(window),
                            window_mode=mode[0],
                        )
                        saved = insert_results(result["records"])
                    st.success(
                        f"Run complete. Saved **{saved:,}** rows "
                        f"({result['processed_timestamps']} timestamps × {result['tags_count']} tags)."
                    )
                except Exception as e:
                    st.exception(e)

    st.subheader("Saved rolling runs")
    runs = list_runs()
    if not runs:
        st.info("No rolling runs found in SQLite yet.")
        return

    run_options = {f"{r['created_at']} | {r['dataset_name']} | {r['run_id'][:10]}": r["run_id"] for r in runs}
    selected = st.selectbox("Select run", options=list(run_options.keys()))
    run_id = run_options[selected]
    rows = load_run_results(run_id)
    if not rows:
        st.warning("No rows found for selected run.")
        return
    rdf = pd.DataFrame(rows)
    rdf["ts"] = pd.to_datetime(rdf["ts"], errors="coerce")

    t1, t2, t3 = st.columns(3)
    t1.metric("Total records", f"{len(rdf):,}")
    t2.metric("Outliers", f"{int((rdf['status'] == 'Outlier').sum()):,}")
    t3.metric("Tags", f"{rdf['tag_name'].nunique():,}")

    tags = sorted(rdf["tag_name"].dropna().astype(str).unique().tolist())
    tag = st.selectbox("Select tag", options=tags, key="rolling_tag")
    sub = rdf[rdf["tag_name"].astype(str) == str(tag)].sort_values("ts")

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=sub["ts"],
            y=sub["tag_value"],
            mode="lines",
            name="Tag Value",
            line=dict(width=1.3),
        )
    )
    out = sub[sub["status"] == "Outlier"]
    if not out.empty:
        fig.add_trace(
            go.Scatter(
                x=out["ts"],
                y=out["tag_value"],
                mode="markers",
                name="Outlier",
                marker=dict(color="#d62728", size=8, symbol="circle"),
                text=out["reason"],
                hovertemplate="%{x}<br>Value=%{y}<br>%{text}<extra></extra>",
            )
        )
    fig.update_layout(
        title=f"{tag} — normal vs outlier behavior",
        height=420,
        margin=dict(l=40, r=20, t=45, b=40),
        legend_orientation="h",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Processed results")
    st.dataframe(
        sub[
            [
                "ts",
                "tag_name",
                "tag_value",
                "baseline_mean",
                "baseline_std",
                "z_score",
                "lower_limit",
                "upper_limit",
                "status",
                "reason",
            ]
        ],
        use_container_width=True,
        height=360,
    )
    st.download_button(
        "Download processed results (CSV)",
        data=sub.to_csv(index=False).encode("utf-8"),
        file_name=f"rolling_outlier_{run_id}_{tag}.csv",
        mime="text/csv",
    )


def main() -> None:
    st.set_page_config(
        page_title="Dev Outlier Detection",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _init_session()

    st.sidebar.title("Industrial AI — Outlier Suite")
    tab = st.sidebar.radio(
        "Select module",
        options=["Dev Outlier Detection", "Rolling Outlier Detection"],
        index=0,
    )
    if tab == "Rolling Outlier Detection":
        _render_rolling_outlier_tab()
        return

    st.title("Dev Outlier Detection")
    st.markdown(
        "Upload plant/process time series (``.xlsx``), configure **plant status**, optional **MFI/DOL**, "
        "then **critical tags** with per-tag thresholds, signal engines, direction, and spike controls. "
        "Only filtered data are passed to the detector."
    )

    up = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
    if up is not None:
        df, err = load_uploaded_streamlit_file(up)
        if err:
            st.error(err)
            st.session_state.wide_df = None
            st.session_state.load_error = err
        else:
            st.session_state.wide_df = df
            st.session_state.load_error = None
            st.success(f"Loaded **{len(df):,}** rows × **{len(df.columns)}** columns.")

    df = st.session_state.wide_df
    if df is None:
        st.info("Upload a workbook to begin.")
        return

    tag_cols = _numeric_tag_columns(df)
    if not tag_cols:
        st.error("No numeric tag columns found after ingest.")
        return

    ok_ts, ts_msg = validate_timestamp_column(df, "Timestamp")
    if not ok_ts:
        st.error(ts_msg)
        return
    if "invalid" in ts_msg.lower():
        st.warning(ts_msg)

    st.subheader("1) Plant status filter (drops matching rows before detection)")
    c1, c2, c3, c4, c5 = st.columns([1, 2, 1, 1, 1])
    with c1:
        ps_en = st.checkbox("Enable plant status filter", value=False, key="ps_en")
    plant_filter: Optional[Dict[str, Any]] = None
    if ps_en:
        with c2:
            ps_tag = st.selectbox("Plant status tag / column", options=tag_cols, key="ps_tag")
        with c3:
            ps_op = st.selectbox("Operator", options=OPS, index=OPS.index("<="), key="ps_op")
        with c4:
            ps_val = st.text_input("Threshold value", value="1", key="ps_val")
        with c5:
            st.caption("Rows where (tag op value) is **true** are removed.")
        try:
            v_conv: Any = float(ps_val)
        except ValueError:
            v_conv = ps_val
        plant_filter = {
            "enabled": True,
            "status_tag": ps_tag,
            "operator": ps_op,
            "value": v_conv,
        }

    st.subheader("2) MFI / DOL filters (optional, OR across enabled blocks)")
    ac1, ac2 = st.columns(2)
    additional: Dict[str, Any] = {}
    with ac1:
        st.markdown("**MFI**")
        mfi_en = st.checkbox("Enable MFI filter", value=False, key="mfi_en")
        mfi_tag = st.selectbox("MFI tag", options=tag_cols, key="mfi_tag")
        mfi_op = st.selectbox("MFI operator", options=OPS, key="mfi_op")
        mfi_val = st.text_input("MFI threshold", value="0", key="mfi_val")
    with ac2:
        st.markdown("**DOL**")
        dol_en = st.checkbox("Enable DOL filter", value=False, key="dol_en")
        dol_tag = st.selectbox("DOL tag", options=tag_cols, key="dol_tag")
        dol_op = st.selectbox("DOL operator", options=OPS, key="dol_op")
        dol_val = st.text_input("DOL threshold", value="0", key="dol_val")

    def _parse_val(s: str) -> Any:
        try:
            return float(s)
        except ValueError:
            return s

    if mfi_en:
        additional["MFI"] = {
            "enabled": True,
            "tag": mfi_tag,
            "operator": mfi_op,
            "value": _parse_val(str(mfi_val)),
        }
    if dol_en:
        additional["DOL"] = {
            "enabled": True,
            "tag": dol_tag,
            "operator": dol_op,
            "value": _parse_val(str(dol_val)),
        }
    additional_opt: Optional[Dict[str, Any]] = additional if additional else None

    st.subheader("3) Critical tags & per-tag configuration")
    default_z = float(MULTI_SIGNAL_PRESET.get("k_global_robust_z", 3.0))
    critical = st.multiselect(
        "Select critical tags (only these appear in the compact results table by default)",
        options=tag_cols,
        default=st.session_state.get("last_critical") or [],
        key="critical_ms",
    )
    st.session_state.last_critical = critical

    tag_config: Dict[str, Dict[str, Any]] = {}
    for tag in critical:
        st.markdown(f"#### `{tag}`")
        k_th = _safe_key(tag, "th")
        k_eng = _safe_key(tag, "eng")
        k_dir = _safe_key(tag, "dir")
        k_ign = _safe_key(tag, "spike_ign")
        k_pers = _safe_key(tag, "spike_pers")
        k_win = _safe_key(tag, "spike_win")
        rc1, rc2, rc3 = st.columns(3)
        with rc1:
            thr = st.number_input(
                "Threshold (reference robust-z scale)",
                min_value=0.1,
                max_value=20.0,
                value=default_z,
                step=0.05,
                key=k_th,
                help="Scales per-signal k-limits vs preset reference (same as Flask Dev tab).",
            )
        with rc2:
            engines = st.multiselect(
                "Signal engines",
                options=ENGINE_OPTIONS_ORDERED,
                default=ENGINE_OPTIONS_ORDERED,
                key=k_eng,
            )
        with rc3:
            direc = st.selectbox("Direction", options=DIR_OPTIONS, index=0, key=k_dir)
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            ign1 = st.checkbox("Ignore single-point spike", value=True, key=k_ign)
        with sc2:
            pers = st.number_input("Spike persistence (points)", min_value=1, max_value=50, value=2, key=k_pers)
        with sc3:
            win = st.number_input("Return-to-normal window (rows)", min_value=1, max_value=100, value=3, key=k_win)
        tag_config[tag] = {
            "threshold": float(thr),
            "selected_engines": list(engines),
            "direction": direc.lower().replace(" only", "").strip(),
            "spike_control": {
                "ignore_single_point_spike": bool(ign1),
                "spike_persistence_points": int(pers),
                "spike_return_to_normal_window": int(win),
            },
        }

    st.subheader("4) Filtered data preview")
    preview_df = apply_plant_status_filter(df, plant_filter)
    preview_df = apply_additional_filters(preview_df, additional_opt)
    dropped = len(df) - len(preview_df)
    m1, m2, m3 = st.columns(3)
    m1.metric("Rows before filters", f"{len(df):,}")
    m2.metric("Rows after filters", f"{len(preview_df):,}")
    m3.metric("Dropped rows", f"{dropped:,}")
    st.dataframe(preview_df.head(200), use_container_width=True, height=320)

    st.subheader("5) Run detection")
    if st.button("Run Dev outlier detection", type="primary"):
        try:
            with st.spinner("Running multi-signal consensus (MULTI_SIGNAL_PRESET)…"):
                bundle = run_multi_signal_outlier_detection(
                    df,
                    tag_config if critical else None,
                    plant_status_filter=plant_filter,
                    additional_filters=additional_opt,
                    critical_tags=critical if critical else None,
                    config=MULTI_SIGNAL_PRESET,
                )
            st.session_state.result_bundle = bundle
            st.success("Run complete.")
        except Exception as e:
            st.session_state.result_bundle = None
            st.exception(e)

    bundle = st.session_state.result_bundle
    if not bundle:
        return

    meta = bundle.get("streamlit_meta") or {}
    st.subheader("6) Applied configuration (echo)")
    st.json(
        {
            "plant_status_filter": meta.get("plant_status_filter"),
            "additional_filters": meta.get("additional_filters"),
            "tag_config": meta.get("tag_config_echo"),
            "timestamp_note": meta.get("timestamp_validation_message"),
        }
    )

    st.subheader("7) Outlier summary & reasons")
    summ = bundle.get("summary") or {}
    if summ:
        st.dataframe(pd.DataFrame([{"Metric": k, "Value": v} for k, v in summ.items()]), height=400, use_container_width=True)

    rows_flat: List[Dict[str, Any]] = []
    for tag, drows in (bundle.get("details_by_tag") or {}).items():
        for r in drows or []:
            one = dict(r)
            one["Tag"] = tag
            rows_flat.append(one)
    if rows_flat:
        st.dataframe(pd.DataFrame(rows_flat), use_container_width=True, height=420)
    else:
        st.info("No abnormal rows for the selected critical tags / configuration.")

    st.subheader("8) Charts (filtered wide data + event markers)")
    wide = bundle.get("df_for_script")
    odf = bundle.get("out_df")
    if isinstance(wide, pd.DataFrame) and not wide.empty:
        plot_tags = critical if critical else tag_cols[: min(6, len(tag_cols))]
        for tg in plot_tags:
            if tg not in wide.columns:
                continue
            fig = go.Figure()
            ts = pd.to_datetime(wide["Timestamp"], errors="coerce")
            fig.add_trace(
                go.Scatter(
                    x=ts,
                    y=pd.to_numeric(wide[tg], errors="coerce"),
                    mode="lines",
                    name=tg,
                    line=dict(width=1.2),
                )
            )
            if isinstance(odf, pd.DataFrame) and not odf.empty and "Tag" in odf.columns:
                sub = odf[odf["Tag"].astype(str) == str(tg)]
                if not sub.empty:
                    fig.add_trace(
                        go.Scatter(
                            x=pd.to_datetime(sub["Timestamp"], errors="coerce"),
                            y=sub["Value"],
                            mode="markers",
                            name="events",
                            marker=dict(size=8, opacity=0.75),
                        )
                    )
            fig.update_layout(
                title=f"{tg} — time series (post-filter input)",
                height=360,
                margin=dict(l=40, r=20, t=50, b=40),
                legend_orientation="h",
            )
            st.plotly_chart(fig, use_container_width=True)


if __name__ == "__main__":
    main()
