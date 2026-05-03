-- Persisted outlier detection (V5 / without_causal_clean_deviation_spike_change_v5) for Live Outlier Excel uploads.
-- One completed run per successful upload analysis; details for UI and optional day filtering.

CREATE TABLE IF NOT EXISTS live_outlier_analysis_run (
    id BIGINT NOT NULL AUTO_INCREMENT,
    dataset_id BIGINT NOT NULL,
    started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    error_message TEXT NULL,
    summary_json JSON NULL,
    artifacts_json JSON NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_lo_run_dataset FOREIGN KEY (dataset_id) REFERENCES live_outlier_excel_dataset (id) ON DELETE CASCADE,
    KEY idx_lo_run_dataset_started (dataset_id, started_at),
    KEY idx_lo_run_dataset_status (dataset_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS live_outlier_analysis_tag_summary (
    id BIGINT NOT NULL AUTO_INCREMENT,
    run_id BIGINT NOT NULL,
    tag_name VARCHAR(512) NOT NULL,
    status VARCHAR(256) NULL,
    first_drift_at DATETIME(6) NULL,
    num_abnormal_rows INT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT fk_lo_tag_run FOREIGN KEY (run_id) REFERENCES live_outlier_analysis_run (id) ON DELETE CASCADE,
    KEY idx_lo_tag_run (run_id),
    KEY idx_lo_tag_run_tag (run_id, tag_name(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS live_outlier_analysis_detail (
    id BIGINT NOT NULL AUTO_INCREMENT,
    run_id BIGINT NOT NULL,
    tag_name VARCHAR(512) NOT NULL,
    observed_at DATETIME(6) NULL,
    actual_value DOUBLE NULL,
    predicted_value DOUBLE NULL,
    final_class VARCHAR(256) NULL,
    direction VARCHAR(256) NULL,
    reason TEXT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_lo_det_run FOREIGN KEY (run_id) REFERENCES live_outlier_analysis_run (id) ON DELETE CASCADE,
    KEY idx_lo_det_run_tag_ts (run_id, tag_name(191), observed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
