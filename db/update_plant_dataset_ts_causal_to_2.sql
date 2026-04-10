-- Point every plant at timeseries_dataset id=2 and causal_dataset id=2 (Live Dashboard / scheduler).
-- Requires rows with id=2 in timeseries_dataset and causal_dataset (FK will fail otherwise).

UPDATE plant_dataset
SET timeseries_dataset_id = 2,
    causal_dataset_id = 2;
