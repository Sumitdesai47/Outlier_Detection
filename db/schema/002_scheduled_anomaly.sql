-- Hourly scheduled anomaly (drift) runs from DB-backed time-series + causal data

CREATE TABLE IF NOT EXISTS scheduled_anomaly_job (
    id BIGINT NOT NULL AUTO_INCREMENT,
    hour_bucket DATETIME(6) NOT NULL COMMENT 'UTC start of hour',
    timeseries_dataset_id BIGINT NULL,
    causal_dataset_id BIGINT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    error_message TEXT NULL,
    summary JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_scheduled_anomaly_hour (hour_bucket),
    KEY idx_scheduled_anomaly_status (status, hour_bucket),
    CONSTRAINT fk_sched_ts FOREIGN KEY (timeseries_dataset_id) REFERENCES timeseries_dataset (id) ON DELETE SET NULL,
    CONSTRAINT fk_sched_causal FOREIGN KEY (causal_dataset_id) REFERENCES causal_dataset (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS scheduled_anomaly_drift (
    id BIGINT NOT NULL AUTO_INCREMENT,
    job_id BIGINT NOT NULL,
    rank_order INT NOT NULL,
    tag VARCHAR(512) NOT NULL,
    drift_score DOUBLE NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sched_drift_rank (job_id, rank_order),
    KEY idx_sched_drift_job (job_id),
    CONSTRAINT fk_sched_drift_job FOREIGN KEY (job_id) REFERENCES scheduled_anomaly_job (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
