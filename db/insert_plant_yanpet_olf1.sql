-- Ensure plant_dataset row: dataset_id = 2, plant_name = 'Yanpet OLF1'
-- Safe to run multiple times (updates plant_name if dataset_id 2 already exists).
-- FK checks off: allows insert when child tables already reference dataset_id = 2.

SET @__fk := @@FOREIGN_KEY_CHECKS;
SET FOREIGN_KEY_CHECKS = 0;

INSERT INTO plant_dataset (dataset_id, plant_name)
VALUES (2, 'Yanpet OLF1')
ON DUPLICATE KEY UPDATE plant_name = VALUES(plant_name);

SET FOREIGN_KEY_CHECKS = @__fk;
