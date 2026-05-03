-- Simple Live Outlier uploads: display name + Excel time series only (no plant / causal).
-- Used by /outlier-excel-upload and Live Outlier detection when selecting an uploaded dataset.

CREATE TABLE IF NOT EXISTS live_outlier_excel_dataset (
    id BIGINT NOT NULL AUTO_INCREMENT,
    dataset_name VARCHAR(512) NOT NULL,
    original_filename VARCHAR(512) NOT NULL,
    uploaded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_lo_excel_ds_uploaded (uploaded_at),
    KEY idx_lo_excel_ds_name (dataset_name(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS live_outlier_excel_observation (
    id BIGINT NOT NULL AUTO_INCREMENT,
    dataset_id BIGINT NOT NULL,
    row_index INT NOT NULL,
    observed_at DATETIME(6) NULL,
    observed_at_raw TEXT NULL,
    tag_name VARCHAR(512) NOT NULL,
    value DOUBLE NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_lo_excel_obs (dataset_id, row_index, tag_name),
    CONSTRAINT fk_lo_excel_obs_dataset FOREIGN KEY (dataset_id) REFERENCES live_outlier_excel_dataset (id) ON DELETE CASCADE,
    KEY idx_lo_excel_obs_dataset_tag_ts (dataset_id, tag_name, observed_at),
    KEY idx_lo_excel_obs_dataset_row (dataset_id, row_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
