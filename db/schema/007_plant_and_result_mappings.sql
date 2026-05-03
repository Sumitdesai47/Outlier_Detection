-- Add plant/result mapping fields and causal_matrix alias column.
-- Safe to re-run (idempotent with information_schema checks).

-- plant_dataset.causal_matrix_dataset_id (alias-style column requested by UI/ops)
SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'plant_dataset' AND COLUMN_NAME = 'causal_matrix_dataset_id') = 0,
    'ALTER TABLE plant_dataset ADD COLUMN causal_matrix_dataset_id BIGINT NULL DEFAULT NULL AFTER timeseries_dataset_id',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

-- Ensure anomaly_drift_result has plant_dataset_id.
SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomaly_drift_result' AND COLUMN_NAME = 'plant_dataset_id') = 0,
    'ALTER TABLE anomaly_drift_result ADD COLUMN plant_dataset_id BIGINT NULL DEFAULT NULL AFTER run_id',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

UPDATE anomaly_drift_result d
JOIN anomaly_run r ON r.id = d.run_id
SET d.plant_dataset_id = r.plant_dataset_id
WHERE d.plant_dataset_id IS NULL AND r.plant_dataset_id IS NOT NULL;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomaly_drift_result' AND INDEX_NAME = 'idx_anomaly_drift_plant') = 0,
    'ALTER TABLE anomaly_drift_result ADD KEY idx_anomaly_drift_plant (plant_dataset_id)',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'anomaly_drift_result' AND CONSTRAINT_NAME = 'fk_anomaly_drift_plant') = 0,
    'ALTER TABLE anomaly_drift_result ADD CONSTRAINT fk_anomaly_drift_plant FOREIGN KEY (plant_dataset_id) REFERENCES plant_dataset (dataset_id) ON DELETE SET NULL',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

-- Ensure anomaly_root_cause_result has plant_dataset_id.
SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomaly_root_cause_result' AND COLUMN_NAME = 'plant_dataset_id') = 0,
    'ALTER TABLE anomaly_root_cause_result ADD COLUMN plant_dataset_id BIGINT NULL DEFAULT NULL AFTER run_id',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

UPDATE anomaly_root_cause_result rr
JOIN anomaly_run r ON r.id = rr.run_id
SET rr.plant_dataset_id = r.plant_dataset_id
WHERE rr.plant_dataset_id IS NULL AND r.plant_dataset_id IS NOT NULL;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomaly_root_cause_result' AND INDEX_NAME = 'idx_anomaly_root_plant') = 0,
    'ALTER TABLE anomaly_root_cause_result ADD KEY idx_anomaly_root_plant (plant_dataset_id)',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'anomaly_root_cause_result' AND CONSTRAINT_NAME = 'fk_anomaly_root_plant') = 0,
    'ALTER TABLE anomaly_root_cause_result ADD CONSTRAINT fk_anomaly_root_plant FOREIGN KEY (plant_dataset_id) REFERENCES plant_dataset (dataset_id) ON DELETE SET NULL',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

-- Ensure outlier_monthly_page has plant_dataset_id.
SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'outlier_monthly_page' AND COLUMN_NAME = 'plant_dataset_id') = 0,
    'ALTER TABLE outlier_monthly_page ADD COLUMN plant_dataset_id BIGINT NULL DEFAULT NULL AFTER run_id',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

UPDATE outlier_monthly_page m
JOIN outlier_run r ON r.id = m.run_id
SET m.plant_dataset_id = r.plant_dataset_id
WHERE m.plant_dataset_id IS NULL AND r.plant_dataset_id IS NOT NULL;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'outlier_monthly_page' AND INDEX_NAME = 'idx_outlier_month_plant') = 0,
    'ALTER TABLE outlier_monthly_page ADD KEY idx_outlier_month_plant (plant_dataset_id)',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'outlier_monthly_page' AND CONSTRAINT_NAME = 'fk_outlier_month_plant') = 0,
    'ALTER TABLE outlier_monthly_page ADD CONSTRAINT fk_outlier_month_plant FOREIGN KEY (plant_dataset_id) REFERENCES plant_dataset (dataset_id) ON DELETE SET NULL',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'plant_dataset' AND INDEX_NAME = 'idx_plant_causal_matrix_dataset') = 0,
    'ALTER TABLE plant_dataset ADD KEY idx_plant_causal_matrix_dataset (causal_matrix_dataset_id)',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'plant_dataset' AND CONSTRAINT_NAME = 'fk_plant_causal_matrix_dataset') = 0,
    'ALTER TABLE plant_dataset ADD CONSTRAINT fk_plant_causal_matrix_dataset FOREIGN KEY (causal_matrix_dataset_id) REFERENCES causal_dataset (id) ON DELETE SET NULL',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

-- Backfill alias column from legacy column when possible.
UPDATE plant_dataset
SET causal_matrix_dataset_id = causal_dataset_id
WHERE causal_matrix_dataset_id IS NULL AND causal_dataset_id IS NOT NULL;

-- Ensure anomaly_run has plant_dataset_id.
SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomaly_run' AND COLUMN_NAME = 'plant_dataset_id') = 0,
    'ALTER TABLE anomaly_run ADD COLUMN plant_dataset_id BIGINT NULL DEFAULT NULL AFTER causal_dataset_id',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'anomaly_run' AND INDEX_NAME = 'idx_anomaly_run_plant') = 0,
    'ALTER TABLE anomaly_run ADD KEY idx_anomaly_run_plant (plant_dataset_id)',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'anomaly_run' AND CONSTRAINT_NAME = 'fk_anomaly_run_plant') = 0,
    'ALTER TABLE anomaly_run ADD CONSTRAINT fk_anomaly_run_plant FOREIGN KEY (plant_dataset_id) REFERENCES plant_dataset (dataset_id) ON DELETE SET NULL',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

-- Ensure outlier_run has plant_dataset_id.
SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'outlier_run' AND COLUMN_NAME = 'plant_dataset_id') = 0,
    'ALTER TABLE outlier_run ADD COLUMN plant_dataset_id BIGINT NULL DEFAULT NULL AFTER timeseries_dataset_id',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'outlier_run' AND INDEX_NAME = 'idx_outlier_run_plant') = 0,
    'ALTER TABLE outlier_run ADD KEY idx_outlier_run_plant (plant_dataset_id)',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;

SET @ddl := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'outlier_run' AND CONSTRAINT_NAME = 'fk_outlier_run_plant') = 0,
    'ALTER TABLE outlier_run ADD CONSTRAINT fk_outlier_run_plant FOREIGN KEY (plant_dataset_id) REFERENCES plant_dataset (dataset_id) ON DELETE SET NULL',
    'SELECT 1'
  )
);
PREPARE _ps FROM @ddl;
EXECUTE _ps;
DEALLOCATE PREPARE _ps;
