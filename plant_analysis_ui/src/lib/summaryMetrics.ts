import type { ResultStatus, ResultSummary, TagSummaryRow } from "@/types/results";

/** Exclusive outlier-only points for a tag. */
export function tagOutlierExclusive(row: TagSummaryRow): number {
  return row.outlier ?? row.outlier_only ?? 0;
}

/** Exclusive process-issue-only points for a tag. */
export function tagProcessExclusive(row: TagSummaryRow): number {
  return row.process ?? row.process_issue_only ?? 0;
}

/** Both column = Outlier + Process (exclusive counts). */
export function tagCombinedAbnormal(row: TagSummaryRow): number {
  if (typeof row.both === "number") return row.both;
  return tagOutlierExclusive(row) + tagProcessExclusive(row);
}

const STATUS_DISTRIBUTION_ORDER: ResultStatus[] = [
  "Normal",
  "Outlier Only",
  "Process Issue Only",
];

export function visibleStatusDistribution(
  distribution: Partial<Record<ResultStatus, number>>,
): Array<{ status: ResultStatus; count: number }> {
  return STATUS_DISTRIBUTION_ORDER.map((status) => ({
    status,
    count: distribution[status] ?? 0,
  })).filter((item) => item.count > 0);
}

export function summaryCombinedAbnormal(summary: ResultSummary): number {
  if (summary.total_abnormal_points != null) return summary.total_abnormal_points;
  return summary.total_outlier_points + summary.total_process_issue_points;
}
