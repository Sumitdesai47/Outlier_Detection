-- Live Dashboard: scheduled jobs keyed by (plant_dataset_id, hour_bucket).
-- Idempotent: safe to re-run if a previous attempt stopped mid-migration (e.g. duplicate column).

-- plant_dataset: optional links to legacy timeseries_dataset / causal_dataset
SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'plant_dataset' AND COLUMN_NAME = 'timeseries_dataset_id') = 0,
    'ALTER TABLE plant_dataset ADD COLUMN timeseries_dataset_id BIGINT NULL DEFAULT NULL AFTER plant_name',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'plant_dataset' AND COLUMN_NAME = 'causal_dataset_id') = 0,
    'ALTER TABLE plant_dataset ADD COLUMN causal_dataset_id BIGINT NULL DEFAULT NULL AFTER timeseries_dataset_id',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'plant_dataset' AND INDEX_NAME = 'idx_plant_ts_dataset') = 0,
    'ALTER TABLE plant_dataset ADD KEY idx_plant_ts_dataset (timeseries_dataset_id)',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'plant_dataset' AND INDEX_NAME = 'idx_plant_causal_dataset') = 0,
    'ALTER TABLE plant_dataset ADD KEY idx_plant_causal_dataset (causal_dataset_id)',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'plant_dataset' AND CONSTRAINT_NAME = 'fk_plant_timeseries_dataset') = 0,
    'ALTER TABLE plant_dataset ADD CONSTRAINT fk_plant_timeseries_dataset FOREIGN KEY (timeseries_dataset_id) REFERENCES timeseries_dataset (id) ON DELETE SET NULL',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'plant_dataset' AND CONSTRAINT_NAME = 'fk_plant_causal_dataset') = 0,
    'ALTER TABLE plant_dataset ADD CONSTRAINT fk_plant_causal_dataset FOREIGN KEY (causal_dataset_id) REFERENCES causal_dataset (id) ON DELETE SET NULL',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

-- scheduled_anomaly_job.plant_dataset_id
SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'scheduled_anomaly_job' AND COLUMN_NAME = 'plant_dataset_id') = 0,
    'ALTER TABLE scheduled_anomaly_job ADD COLUMN plant_dataset_id BIGINT NULL DEFAULT NULL AFTER causal_dataset_id',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'scheduled_anomaly_job' AND INDEX_NAME = 'idx_scheduled_job_plant') = 0,
    'ALTER TABLE scheduled_anomaly_job ADD KEY idx_scheduled_job_plant (plant_dataset_id)',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

INSERT INTO plant_dataset (plant_name)
SELECT 'Default plant'
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM plant_dataset LIMIT 1);

UPDATE scheduled_anomaly_job j
SET j.plant_dataset_id = (SELECT MIN(p.dataset_id) FROM plant_dataset p)
WHERE j.plant_dataset_id IS NULL;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'scheduled_anomaly_job' AND INDEX_NAME = 'uq_scheduled_anomaly_hour') > 0,
    'ALTER TABLE scheduled_anomaly_job DROP INDEX uq_scheduled_anomaly_hour',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

UPDATE scheduled_anomaly_job j
SET j.plant_dataset_id = (SELECT MIN(p.dataset_id) FROM plant_dataset p)
WHERE j.plant_dataset_id IS NULL;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'scheduled_anomaly_job' AND COLUMN_NAME = 'plant_dataset_id' AND IS_NULLABLE = 'YES') > 0,
    'ALTER TABLE scheduled_anomaly_job MODIFY COLUMN plant_dataset_id BIGINT NOT NULL',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'scheduled_anomaly_job' AND INDEX_NAME = 'uq_scheduled_anomaly_plant_hour') = 0,
    'ALTER TABLE scheduled_anomaly_job ADD UNIQUE KEY uq_scheduled_anomaly_plant_hour (plant_dataset_id, hour_bucket)',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'scheduled_anomaly_job' AND CONSTRAINT_NAME = 'fk_scheduled_job_plant') = 0,
    'ALTER TABLE scheduled_anomaly_job ADD CONSTRAINT fk_scheduled_job_plant FOREIGN KEY (plant_dataset_id) REFERENCES plant_dataset (dataset_id) ON DELETE CASCADE',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;
