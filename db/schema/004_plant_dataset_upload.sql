-- Plant-scoped upload store: one plant row links to many time-series and causal rows.
-- Variable Excel layouts are stored as JSON per row (assumption: schemas differ by file).

CREATE TABLE IF NOT EXISTS plant_dataset (
    dataset_id BIGINT NOT NULL AUTO_INCREMENT,
    plant_name VARCHAR(512) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (dataset_id),
    UNIQUE KEY uq_plant_dataset_plant_name (plant_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS time_series_data (
    id BIGINT NOT NULL AUTO_INCREMENT,
    dataset_id BIGINT NOT NULL,
    row_index INT NOT NULL,
    row_data JSON NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ts_data_dataset_row (dataset_id, row_index),
    KEY idx_ts_data_dataset (dataset_id),
    CONSTRAINT fk_ts_data_plant FOREIGN KEY (dataset_id) REFERENCES plant_dataset (dataset_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS causal_data (
    id BIGINT NOT NULL AUTO_INCREMENT,
    dataset_id BIGINT NOT NULL,
    sheet_name VARCHAR(255) NOT NULL DEFAULT '',
    row_index INT NOT NULL,
    row_data JSON NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_causal_data_dataset_sheet_row (dataset_id, sheet_name, row_index),
    KEY idx_causal_data_dataset (dataset_id),
    CONSTRAINT fk_causal_data_plant FOREIGN KEY (dataset_id) REFERENCES plant_dataset (dataset_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
