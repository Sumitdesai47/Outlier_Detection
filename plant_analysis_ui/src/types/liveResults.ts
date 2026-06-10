export interface LiveDriftRow {
  rank: number;
  tag: string;
  drift_score: number;
}

export interface LiveRootRow {
  root_cause: string;
  root_cause_score: number;
  propagation_path: string;
}

export interface LiveDashboardOverview {
  run_id: string;
  plant_name: string;
  subsystem: string;
  dataset_name: string;
  engine: string;
  observation_days: string[];
  selected_day: string | null;
  observation_first?: string;
  observation_last?: string;
  drifts: LiveDriftRow[];
  has_outlier_day: boolean;
  has_detail_rows_for_day: boolean;
  summary?: Record<string, unknown>;
  error?: string;
}

export interface LiveTagDetail {
  tag: string;
  drift_score: number | null;
  roots: LiveRootRow[];
  roots_error: string | null;
  plot: { data: unknown[]; layout: Record<string, unknown> };
}
