CREATE TABLE IF NOT EXISTS rolling_outlier_run (
  id BIGINT NOT NULL AUTO_INCREMENT,
  timeseries_dataset_id BIGINT NOT NULL,
  dataset_name VARCHAR(255) NOT NULL,
  window_size INT NOT NULL,
  window_mode ENUM('rolling', 'expanding') NOT NULL DEFAULT 'rolling',
  baseline_rows INT NOT NULL DEFAULT 30,
  status ENUM('running', 'completed', 'failed') NOT NULL DEFAULT 'running',
  error_message TEXT NULL,
  rows_processed INT NOT NULL DEFAULT 0,
  tags_processed INT NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_rolling_run_dataset_created (timeseries_dataset_id, created_at),
  CONSTRAINT fk_rolling_run_dataset
    FOREIGN KEY (timeseries_dataset_id) REFERENCES timeseries_dataset(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rolling_outlier_result (
  id BIGINT NOT NULL AUTO_INCREMENT,
  run_id BIGINT NOT NULL,
  row_index INT NOT NULL,
  observed_at DATETIME NULL,
  observed_at_raw VARCHAR(100) NULL,
  tag_name VARCHAR(255) NOT NULL,
  tag_value DOUBLE NULL,
  baseline_mean DOUBLE NULL,
  baseline_std DOUBLE NULL,
  z_score DOUBLE NULL,
  lower_limit DOUBLE NULL,
  upper_limit DOUBLE NULL,
  status ENUM('Normal', 'Outlier') NOT NULL DEFAULT 'Normal',
  reason TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_rolling_result_run_tag_time (run_id, tag_name, observed_at),
  KEY idx_rolling_result_run_row_tag (run_id, row_index, tag_name),
  CONSTRAINT fk_rolling_result_run
    FOREIGN KEY (run_id) REFERENCES rolling_outlier_run(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
