import type { ResultPoint } from "@/types/results";

export type IssueCategory = "tag" | "process";

/** Mirror services/plant_analysis_results_store.issue_category */
export function issueCategory(point: ResultPoint): IssueCategory | null {
  if (point.status === "Normal") return null;

  if (point.s5_peer_fired === true) return "tag";
  if (point.s5_peer_fired === false) return "process";

  if (point.status === "Both") return "tag";
  if (point.status === "Outlier Only") return "process";
  if (point.status === "Process Issue Only") return "process";
  return "tag";
}

export function issueCategoryLabel(category: IssueCategory): string {
  return category === "tag" ? "Tag issue" : "Process issue";
}

export function pointMatchesTab(point: ResultPoint, tab: string): boolean {
  if (tab === "both") return point.status !== "Normal";
  if (tab === "outlier") return issueCategory(point) === "tag";
  if (tab === "process") return issueCategory(point) === "process";
  return false;
}
