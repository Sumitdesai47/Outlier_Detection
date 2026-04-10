-- Anomaly Detection dashboard — MySQL 8+ schema (database: anomaly)
-- Apply: python scripts/init_db.py

CREATE TABLE IF NOT EXISTS timeseries_dataset (
    id BIGINT NOT NULL AUTO_INCREMENT,
    original_filename VARCHAR(512) NOT NULL,
    uploaded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    timestamp_column VARCHAR(255) NOT NULL DEFAULT 'Timestamp',
    tag_names JSON NOT NULL,
    row_count INT NOT NULL,
    meta JSON NOT NULL,
    content_sha256 VARCHAR(64) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_timeseries_content_sha256 (content_sha256),
    KEY idx_timeseries_dataset_uploaded (uploaded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS timeseries_observation (
    id BIGINT NOT NULL AUTO_INCREMENT,
    dataset_id BIGINT NOT NULL,
    row_index INT NOT NULL,
    observed_at DATETIME(6) NULL,
    observed_at_raw TEXT NULL,
    tag_name VARCHAR(512) NOT NULL,
    value DOUBLE NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_timeseries_obs (dataset_id, row_index, tag_name),
    CONSTRAINT fk_ts_obs_dataset FOREIGN KEY (dataset_id) REFERENCES timeseries_dataset (id) ON DELETE CASCADE,
    KEY idx_timeseries_obs_dataset_tag_ts (dataset_id, tag_name, observed_at),
    KEY idx_timeseries_obs_dataset_row (dataset_id, row_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS causal_dataset (
    id BIGINT NOT NULL AUTO_INCREMENT,
    original_filename VARCHAR(512) NOT NULL,
    uploaded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    meta JSON NOT NULL,
    content_sha256 VARCHAR(64) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_causal_content_sha256 (content_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS causal_sheet (
    id BIGINT NOT NULL AUTO_INCREMENT,
    dataset_id BIGINT NOT NULL,
    sheet_name VARCHAR(255) NOT NULL,
    row_count INT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uq_causal_sheet (dataset_id, sheet_name),
    CONSTRAINT fk_causal_sheet_dataset FOREIGN KEY (dataset_id) REFERENCES causal_dataset (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS causal_row (
    id BIGINT NOT NULL AUTO_INCREMENT,
    sheet_id BIGINT NOT NULL,
    excel_row_number INT NOT NULL,
    propagation_path TEXT NULL,
    row_payload JSON NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_causal_row (sheet_id, excel_row_number),
    CONSTRAINT fk_causal_row_sheet FOREIGN KEY (sheet_id) REFERENCES causal_sheet (id) ON DELETE CASCADE,
    KEY idx_causal_row_sheet (sheet_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS anomaly_run (
    id BIGINT NOT NULL AUTO_INCREMENT,
    result_session_uuid VARCHAR(64) NOT NULL,
    timeseries_dataset_id BIGINT NULL,
    causal_dataset_id BIGINT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    historic_ratio DOUBLE NULL,
    lookback_months INT NULL,
    top_k_drift INT NULL,
    summary JSON NOT NULL,
    status VARCHAR(64) NOT NULL DEFAULT 'completed',
    PRIMARY KEY (id),
    UNIQUE KEY uq_anomaly_run_session (result_session_uuid),
    KEY idx_anomaly_run_created (created_at),
    CONSTRAINT fk_anomaly_run_ts FOREIGN KEY (timeseries_dataset_id) REFERENCES timeseries_dataset (id) ON DELETE SET NULL,
    CONSTRAINT fk_anomaly_run_causal FOREIGN KEY (causal_dataset_id) REFERENCES causal_dataset (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS anomaly_drift_result (
    id BIGINT NOT NULL AUTO_INCREMENT,
    run_id BIGINT NOT NULL,
    rank_order INT NOT NULL,
    tag VARCHAR(512) NOT NULL,
    drift_score DOUBLE NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_anomaly_drift (run_id, rank_order),
    KEY idx_anomaly_drift_run (run_id),
    CONSTRAINT fk_anomaly_drift_run FOREIGN KEY (run_id) REFERENCES anomaly_run (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS anomaly_root_cause_result (
    id BIGINT NOT NULL AUTO_INCREMENT,
    run_id BIGINT NOT NULL,
    target_tag VARCHAR(512) NOT NULL,
    rank_order INT NOT NULL,
    root_cause_tag VARCHAR(512) NOT NULL,
    root_cause_score DOUBLE NULL,
    propagation_path TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_anomaly_root (run_id, target_tag, rank_order),
    KEY idx_anomaly_root_target (run_id, target_tag),
    CONSTRAINT fk_anomaly_root_run FOREIGN KEY (run_id) REFERENCES anomaly_run (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS outlier_run (
    id BIGINT NOT NULL AUTO_INCREMENT,
    result_session_uuid VARCHAR(64) NOT NULL,
    timeseries_dataset_id BIGINT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    tag_summaries JSON NOT NULL,
    details_by_tag JSON NOT NULL,
    status VARCHAR(64) NOT NULL DEFAULT 'completed',
    PRIMARY KEY (id),
    UNIQUE KEY uq_outlier_run_session (result_session_uuid),
    KEY idx_outlier_run_created (created_at),
    CONSTRAINT fk_outlier_run_ts FOREIGN KEY (timeseries_dataset_id) REFERENCES timeseries_dataset (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS outlier_monthly_page (
    id BIGINT NOT NULL AUTO_INCREMENT,
    run_id BIGINT NOT NULL,
    tag_name VARCHAR(512) NOT NULL,
    month_label VARCHAR(128) NOT NULL,
    page_rows JSON NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_outlier_month (run_id, tag_name, month_label),
    KEY idx_outlier_month_run_tag (run_id, tag_name),
    CONSTRAINT fk_outlier_month_run FOREIGN KEY (run_id) REFERENCES outlier_run (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
