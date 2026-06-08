export type ResultTab = "summary" | "outlier" | "process" | "both";

export type ResultStatus =
  | "Normal"
  | "Outlier Only"
  | "Process Issue Only"
  | "Both";

export interface ResultPoint {
  id: number;
  run_id: string;
  tag_name: string;
  observed_at: string | null;
  tag_value: number | null;
  status: ResultStatus;
  outlier_score: number | null;
  process_issue_score: number | null;
  lower_limit: number | null;
  upper_limit: number | null;
  related_tags: string[];
  reason: string | null;
  interpretation: string | null;
  suggested_action: string | null;
  severity: string | null;
}

export interface ResultSummary {
  run_id: string;
  plant_name: string;
  subsystem: string;
  dataset_name: string;
  total_tags_analyzed: number;
  total_records_processed: number;
  total_outlier_points: number;
  total_process_issue_points: number;
  total_abnormal_points?: number;
  analysis_duration: string;
  last_processed_at: string;
  status_distribution: Partial<Record<ResultStatus, number>>;
  tag_summaries: TagSummaryRow[];
}

export interface TagSummaryRow {
  tag_name: string;
  total_points: number;
  /** Exclusive outlier-only points. */
  outlier?: number;
  /** Exclusive process-issue-only points. */
  process?: number;
  /** Outlier + process combined abnormal count. */
  both?: number;
  normal: number;
  /** @deprecated use outlier */
  outlier_only?: number;
  /** @deprecated use process */
  process_issue_only?: number;
  dual_classified?: number;
}

export interface AnalysisRun {
  id: string;
  plant_name: string;
  subsystem: string;
  dataset_name: string;
  processed_at: string;
}

export interface ResultFilters {
  plant: string;
  subsystem: string;
  runId: string;
  tag: string;
}
