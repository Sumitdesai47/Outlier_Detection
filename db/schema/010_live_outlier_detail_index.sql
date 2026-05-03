-- Speed day-range reads on analysis details and reduce filesort work for large runs.
-- Idempotent: skip ADD INDEX when idx_lo_det_run_obs already exists (safe to re-run init_db).
SET @sql_010 = (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.statistics
      WHERE table_schema = DATABASE()
        AND table_name = 'live_outlier_analysis_detail'
        AND index_name = 'idx_lo_det_run_obs') > 0,
    'SELECT 1',
    'ALTER TABLE live_outlier_analysis_detail ADD INDEX idx_lo_det_run_obs (run_id, observed_at)'
  )
);
PREPARE stmt_010 FROM @sql_010;
EXECUTE stmt_010;
DEALLOCATE PREPARE stmt_010;
