-- Store per-tag top root causes for each scheduled anomaly job

CREATE TABLE IF NOT EXISTS scheduled_anomaly_root (
    id BIGINT NOT NULL AUTO_INCREMENT,
    job_id BIGINT NOT NULL,
    target_tag VARCHAR(512) NOT NULL,
    rank_order INT NOT NULL,
    root_cause_tag VARCHAR(512) NOT NULL,
    root_cause_score DOUBLE NULL,
    propagation_path TEXT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sched_root (job_id, target_tag, rank_order),
    KEY idx_sched_root_job_tag (job_id, target_tag),
    CONSTRAINT fk_sched_root_job FOREIGN KEY (job_id) REFERENCES scheduled_anomaly_job (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
