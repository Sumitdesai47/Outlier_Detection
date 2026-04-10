-- Example: register plant name for an existing dataset_id=2 after child rows exist.
-- Preconditions (application rule): at least one row in time_series_data and one in causal_data
-- for dataset_id = 2, and no plant_dataset row with dataset_id = 2.
--
-- If children were inserted with foreign keys enabled, the parent row must exist first;
-- this script is for legacy data loaded with FOREIGN_KEY_CHECKS=0 or equivalent.

SET @fk_prev := @@FOREIGN_KEY_CHECKS;
SET FOREIGN_KEY_CHECKS = 0;

INSERT INTO plant_dataset (dataset_id, plant_name)
SELECT 2, 'Yanpet OLF1'
WHERE EXISTS (SELECT 1 FROM time_series_data WHERE dataset_id = 2 LIMIT 1)
  AND EXISTS (SELECT 1 FROM causal_data WHERE dataset_id = 2 LIMIT 1)
  AND NOT EXISTS (SELECT 1 FROM plant_dataset WHERE dataset_id = 2 LIMIT 1);

SET FOREIGN_KEY_CHECKS = @fk_prev;

-- Inspect: SELECT * FROM plant_dataset WHERE dataset_id = 2;
