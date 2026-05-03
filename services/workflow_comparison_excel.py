"""
Static comparison workbook: Auto (No Causal) vs outlier detection vs outlier detection (using data model).

Used by GET /docs/workflow-comparison-no-causal-v5-v6.xlsx
"""
from __future__ import annotations

from io import BytesIO
from typing import List, Tuple

import pandas as pd


def _sheet_logic_comparison() -> pd.DataFrame:
    rows: List[dict] = [
        {
            "Topic": "Primary question",
            "Part4_Auto_NoCausal": "Is |value z| outside learned symmetric bands from a clean reference window?",
            "Outlier detection": "Is the process outside clean-like limits, or showing spikes / error shifts / persistent in-band deviation vs clean era?",
            "Outlier detection (using data model)": "Is actual inconsistent with a ridge model built from top-5 correlated tags (residual + range + peer context)?",
        },
        {
            "Topic": "Causal matrix",
            "Part4_Auto_NoCausal": "Not used",
            "Outlier detection": "Not used",
            "Outlier detection (using data model)": "Not used",
        },
        {
            "Topic": "Reference / baseline",
            "Part4_Auto_NoCausal": "Auto-detected multivariate clean timestamps; per-tag mean/std (or MAD path) from clean rows → drift / drift+anomaly / strong symmetric limits (z=3.0, 3.5, 5.0).",
            "Outlier detection": "Clean period without moving average; clean-like broad Lower/Upper + central band; deltas and error-change scales from clean reference.",
            "Outlier detection (using data model)": "Stable-row mask (cross-tag robust-z grid); per-target training quantiles (5/95 soft, 1/99 outer) and residual scale from training residuals.",
        },
        {
            "Topic": "Core signals",
            "Part4_Auto_NoCausal": "Univariate Value_Z vs Baseline_Center/Scale only (no explicit spike or peer-residual logic in classifier).",
            "Outlier detection": "Value_Z, Delta_Z, Abs_Error_Change_Z, Deviation_Level_Z; outside limit; within-limit spike; within-limit error-change; persistent in-band deviation (run lengths).",
            "Outlier detection (using data model)": "Value_Z vs training robust center/scale; Residual_Z (actual − ridge prediction); Soft/Outer range flags; Peer_Shift_Fraction across top predictors.",
        },
        {
            "Topic": "Multivariate coupling",
            "Part4_Auto_NoCausal": "Only when auto-selecting clean window (joint stability), not in per-row classification.",
            "Outlier detection": "Implicit via clean score across tags for window choice; per-tag classification still univariate + derivatives of own value.",
            "Outlier detection (using data model)": "Explicit: predictions use top-5 correlated peers each timestep; peer shift supports Drift label.",
        },
        {
            "Topic": "Within-limit anomalies",
            "Part4_Auto_NoCausal": "No separate class: if |z| below drift band → Normal.",
            "Outlier detection": "Yes: within-limit spike / error-change → Drift + Anomaly; persistent deviation outside central band → Drift.",
            "Outlier detection (using data model)": "Yes: Contextual Anomaly = residual break while not in soft range (within-limit vs model).",
        },
        {
            "Topic": "Final class vocabulary",
            "Part4_Auto_NoCausal": "Normal, Drift, Drift + Anomaly, Strong Anomaly (4 labels).",
            "Outlier detection": "Same four; logic differs (persistence + in-limit events).",
            "Outlier detection (using data model)": "Adds Contextual Anomaly (5 labels) for model-residual outliers.",
        },
        {
            "Topic": "Typical false-positive risk",
            "Part4_Auto_NoCausal": "Higher if clean window misrepresents regime (single z ladder can flag sustained new level as strong).",
            "Outlier detection": "Mitigated for one-off spikes vs sustained drift via persistence rules; may still flag volatile tags under tight clean-like limits.",
            "Outlier detection (using data model)": "Correlated noise / collinearity can inflate residual z; strong when R² low on small training.",
        },
        {
            "Topic": "Typical false-negative risk",
            "Part4_Auto_NoCausal": "Sub-threshold coordinated shifts; no within-limit subtlety.",
            "Outlier detection": "Very gradual moves inside central band may stay Normal until persistence triggers.",
            "Outlier detection (using data model)": "If top-5 peers move with target, residual stays small even when process is off (multicollinearity masks fault).",
        },
        {
            "Topic": "Compute cost",
            "Part4_Auto_NoCausal": "Moderate (pivot + clean search + classify).",
            "Outlier detection": "Higher (clean scoring, limits, per-row derivatives, run lengths).",
            "Outlier detection (using data model)": "Highest (per-tag O(N) ridge + corr matrix on stable mask; scales with tag count squared for corr, linear per tag for fit).",
        },
        {
            "Topic": "Best when",
            "Part4_Auto_NoCausal": "You want a simple, auditable univariate z-band detector after an automatic clean reference.",
            "Outlier detection": "You care about step changes, error dynamics, and creeping in-band deviation without a causal model.",
            "Outlier detection (using data model)": "You want relationship-aware alarms (actual vs peers), explicit contextual (model) outliers, and quantile-based range story.",
        },
    ]
    return pd.DataFrame(rows)


def _sheet_final_class_crosswalk() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Final_Class": "Normal",
                "Part4": "Yes",
                "Outlier detection": "Yes",
                "Outlier detection (using data model)": "Yes",
                "Semantic_alignment": "All three: no abnormal decision.",
            },
            {
                "Final_Class": "Drift",
                "Part4": "Yes — |z| in [drift, drift_anomaly)",
                "Outlier detection": "Yes — persistent in-band deviation OR (outside with persistence and lower z)",
                "Outlier detection (using data model)": "Yes — soft/outer range with peer support or residual not anomalous per rules",
                "Semantic_alignment": "Similar English label; **different mechanical definition** — do not compare counts directly without relabeling.",
            },
            {
                "Final_Class": "Drift + Anomaly",
                "Part4": "Yes — |z| in [drift_anomaly, strong)",
                "Outlier detection": "Yes — single-step outside limit (non-persistent), OR within-limit spike/error-change",
                "Outlier detection (using data model)": "Yes — soft range + residual anomaly",
                "Semantic_alignment": "Overlaps 'moderate / compound' severity but triggers differ.",
            },
            {
                "Final_Class": "Strong Anomaly",
                "Part4": "Yes — |z| ≥ strong",
                "Outlier detection": "Yes — |Value_Z| ≥ strong_anomaly_z when outside",
                "Outlier detection (using data model)": "Yes — residual_strong / outer+residual / value_strong+residual (see script classify())",
                "Semantic_alignment": "All mean 'severe' but the data-model path can fire on residual without extreme raw value.",
            },
            {
                "Final_Class": "Contextual Anomaly",
                "Part4": "No — class does not exist",
                "Outlier detection": "No — folded into Drift + Anomaly style within-limit breaks",
                "Outlier detection (using data model)": "Yes — residual anomaly without soft range flag",
                "Semantic_alignment": "Unique to outlier detection (using data model) among these three UIs.",
            },
        ]
    )


def _sheet_signals_matrix() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Signal_or_feature": "Per-row univariate z vs baseline", "Part4": "Yes", "Outlier detection": "Yes (Value_Z)", "Outlier detection (using data model)": "Yes (Value_Z)"},
            {"Signal_or_feature": "Symmetric drift/anomaly/strong limits from clean", "Part4": "Yes (fixed z steps)", "Outlier detection": "Broad limits + central band", "Outlier detection (using data model)": "Quantile soft/outer bands from training ref"},
            {"Signal_or_feature": "First difference (spike) z", "Part4": "No", "Outlier detection": "Yes (Delta_Z)", "Outlier detection (using data model)": "No (not primary)"},
            {"Signal_or_feature": "Second-layer error change z", "Part4": "No", "Outlier detection": "Yes (Error_Change_Z)", "Outlier detection (using data model)": "No"},
            {"Signal_or_feature": "Persistent run-length logic", "Part4": "No", "Outlier detection": "Yes", "Outlier detection (using data model)": "No"},
            {"Signal_or_feature": "Peer / multivariate prediction residual", "Part4": "No", "Outlier detection": "No (UI may overlay LR on pivots)", "Outlier detection (using data model)": "Yes (core)"},
            {"Signal_or_feature": "Top-N correlation feature pick", "Part4": "No (only for optional UI corr table)", "Outlier detection": "No", "Outlier detection (using data model)": "Yes (N=5)"},
            {"Signal_or_feature": "Explicit peer shift vote", "Part4": "No", "Outlier detection": "No", "Outlier detection (using data model)": "Yes (Peer_Shift_Fraction)"},
        ]
    )


def _sheet_threshold_reference() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Parameter_or_gate": "Drift |z| (univariate ladder)",
                "Part4": "3.0 (script defaults)",
                "Outlier detection": "Uses config drift_z / drift_anomaly_z / strong_anomaly_z on Value_Z when outside (see v5 CONFIG)",
                "Outlier detection (using data model)": "Not a single ladder; uses residual_z_limit (3.0), value_z limits, peer_shift_fraction_limit, etc. (DEFAULTS in top5 script)",
            },
            {
                "Parameter_or_gate": "Strong severity",
                "Part4": "5.0 |z|",
                "Outlier detection": "strong_anomaly_z on outside limit path",
                "Outlier detection (using data model)": "residual_strong_z_limit (5.0) and combined rules",
            },
            {
                "Parameter_or_gate": "Within-limit anomaly path",
                "Part4": "N/A",
                "Outlier detection": "delta_spike_z, error_change_z, inlimit_deviation_z + persistence point counts",
                "Outlier detection (using data model)": "Residual vs training residual scale; Contextual Anomaly branch",
            },
        ]
    )


def _sheet_recommendation() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Use_case": "Operations monitoring — simplest explainability",
                "Preferred_workflow": "Part4 Auto (No Causal)",
                "Rationale": "One number (z) vs three bands tied to an automatic clean era; easy to defend in reviews.",
            },
            {
                "Use_case": "Detect sneaky drift inside 'normal' operating band + sharp glitches",
                "Preferred_workflow": "Outlier detection (multi-signal)",
                "Rationale": "Purpose-built for within-limit spike/error-change and persistent deviation; no peer model dependency.",
            },
            {
                "Use_case": "Equipment / process relationships matter; false alarms when only univariate z fires",
                "Preferred_workflow": "Outlier detection (using data model)",
                "Rationale": "Residual against correlated peers suppresses 'everyone moved together' and highlights contextual breaks.",
            },
            {
                "Use_case": "Benchmarking vs labeled All_Results sheet",
                "Preferred_workflow": "Run standalone top5 script with --benchmark_file OR export three runs and merge externally",
                "Rationale": "Built-in benchmark compare exists in without_causal_top5_corr_regression_fast.py; part4/v5 app tabs focus on UI bundle.",
            },
            {
                "Use_case": "Few tags, short history, unstable correlation",
                "Preferred_workflow": "Part4 or multi-signal outlier over data-model outlier",
                "Rationale": "Data-model path needs stable training mask + min_train_rows; weak R² makes residual tests noisy.",
            },
            {
                "Use_case": "Many tags, rich multivariate history",
                "Preferred_workflow": "Outlier detection (using data model), with sanity checks",
                "Rationale": "Exploits cross-tag structure; review Tag_Summary Model_R2_Train in script export.",
            },
        ]
    )


def _sheet_ui_column_mapping() -> pd.DataFrame:
    """How each pipeline feeds the shared results.html / plots layer."""
    return pd.DataFrame(
        [
            {
                "Dashboard_field_or_layer": "df_for_script (wide Actual_Value)",
                "Part4": "Pivot Timestamp × Tag from long_df",
                "Outlier detection": "Same",
                "Outlier detection (using data model)": "Same (from All_Results-style long output pivoted implicitly via row model)",
            },
            {
                "Dashboard_field_or_layer": "out_df Status for markers",
                "Part4": "Maps Final_Class → normal/sudden_jump/mild_outlier/strong_outlier",
                "Outlier detection": "Same mapping",
                "Outlier detection (using data model)": "Same + Contextual Anomaly → mild_outlier (plot parity)",
            },
            {
                "Dashboard_field_or_layer": "details_by_tag Predicted_Value",
                "Part4": "LR/EWMA overlay from pivot + limits",
                "Outlier detection": "LR/EWMA overlay in app (multi-signal script leaves Predicted_Value NaN)",
                "Outlier detection (using data model)": "Ridge prediction from top-5 correlated tags",
            },
            {
                "Dashboard_field_or_layer": "tag_limits_by_tag",
                "Part4": "Drift / drift_anomaly / strong limits from clean baseline",
                "Outlier detection": "Clean-like Lower/Upper mapped into same JSON shape",
                "Outlier detection (using data model)": "Historical quantile soft/outer approximated into drift_* fields for UI",
            },
            {
                "Dashboard_field_or_layer": "x_variables_by_tag",
                "Part4": "Pivot corr top 10",
                "Outlier detection": "Pivot corr top 10",
                "Outlier detection (using data model)": "Parsed from per-tag Top_Correlations string in model output",
            },
            {
                "Dashboard_field_or_layer": "App Excel download (part3 download route)",
                "Part4": "Tag_Summary, Detail_Rows, Monthly_Pages from session export_payload",
                "Outlier detection": "Same schema",
                "Outlier detection (using data model)": "Same schema",
            },
        ]
    )


def _sheet_binary_abnormal_rules() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Workflow": "Part4",
                "Abnormal_if": "Final_Class != Normal (i.e. Drift, Drift + Anomaly, Strong Anomaly)",
                "Notes": "Final_Status = Abnormal for those rows.",
            },
            {
                "Workflow": "Outlier detection",
                "Abnormal_if": "Same four-class set; any non-Normal → Abnormal",
                "Notes": "binary_status() in v5 core mirrors this.",
            },
            {
                "Workflow": "Outlier detection (using data model)",
                "Abnormal_if": "All classes except Normal (includes Contextual Anomaly)",
                "Notes": "binary_status() treats non-normal/non-unknown as Abnormal.",
            },
        ]
    )


def _sheet_plot_status_mapping() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Final_Class": "Normal", "Plot_Status_token": "normal"},
            {"Final_Class": "Drift", "Plot_Status_token": "sudden_jump"},
            {"Final_Class": "Drift + Anomaly", "Plot_Status_token": "mild_outlier"},
            {"Final_Class": "Strong Anomaly", "Plot_Status_token": "strong_outlier"},
            {"Final_Class": "Contextual Anomaly", "Plot_Status_token": "mild_outlier (data model)"},
        ]
    )


def _sheet_decision_matrix_scores() -> pd.DataFrame:
    """Subjective score 1–5 for quick portfolio view (higher = stronger fit). Not a statistical score."""
    criteria = [
        "Explainability to non-data-scientists",
        "Sensitivity to sharp single-timestep faults",
        "Sensitivity to slow creeping drift inside limits",
        "Use of multivariate / peer information",
        "Robustness to short or messy history",
        "Runtime scalability (many tags)",
    ]
    return pd.DataFrame(
        {
            "Criterion": criteria,
            "Part4_score_1_to_5": [5, 3, 2, 2, 4, 4],
            "Outlier_detection_score_1_to_5": [3, 5, 5, 2, 3, 3],
            "Outlier_detection_data_model_score_1_to_5": [3, 4, 3, 5, 2, 2],
        }
    )


def _sheet_merge_instructions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Step": 1,
                "Instruction": "Run each tab (Auto no causal, Outlier detection, Outlier detection using data model) on the **same** XLSX upload.",
            },
            {
                "Step": 2,
                "Instruction": "From each results page, download the Excel export (session-backed) if available, or copy All_Results from standalone script outputs.",
            },
            {
                "Step": 3,
                "Instruction": "Normalize keys: cast Timestamp to datetime (UTC-naive) and Tag to trimmed string.",
            },
            {
                "Step": 4,
                "Instruction": "Inner-join on [Timestamp, Tag]. Add one Final_Class column per tab you ran.",
            },
            {
                "Step": 5,
                "Instruction": "Build pivot tables across engines (e.g. Part4 vs data model) — interpret as **label migration**, not accuracy, because class definitions differ (see Final_Class_Crosswalk sheet).",
            },
        ]
    )


def build_workbook_bytes() -> Tuple[bytes, str]:
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        _sheet_logic_comparison().to_excel(writer, sheet_name="Logic_Comparison", index=False)
        _sheet_signals_matrix().to_excel(writer, sheet_name="Signals_Matrix", index=False)
        _sheet_final_class_crosswalk().to_excel(writer, sheet_name="Final_Class_Crosswalk", index=False)
        _sheet_threshold_reference().to_excel(writer, sheet_name="Threshold_Reference", index=False)
        _sheet_recommendation().to_excel(writer, sheet_name="When_To_Use", index=False)
        _sheet_ui_column_mapping().to_excel(writer, sheet_name="UI_Column_Mapping", index=False)
        _sheet_binary_abnormal_rules().to_excel(writer, sheet_name="Binary_Abnormal_Rules", index=False)
        _sheet_plot_status_mapping().to_excel(writer, sheet_name="Plot_Status_Mapping", index=False)
        _sheet_decision_matrix_scores().to_excel(writer, sheet_name="Heuristic_Scorecard_1_to_5", index=False)
        _sheet_merge_instructions().to_excel(writer, sheet_name="How_To_Merge_Runs", index=False)
        pd.DataFrame(
            [
                {
                    "Title": "Workflow comparison: Auto (no causal) vs outlier detection vs outlier detection (using data model)",
                    "Generated_by": "services/workflow_comparison_excel.py",
                    "Note": "Heuristic_Scorecard is subjective guidance, not calibrated metric. Final_Class labels are not interchangeable across workflows.",
                }
            ]
        ).to_excel(writer, sheet_name="Readme", index=False)
    bio.seek(0)
    return bio.getvalue(), "workflow_comparison_no_causal_v5_v6.xlsx"
