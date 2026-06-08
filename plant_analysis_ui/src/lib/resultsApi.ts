import type { AnalysisRun, ResultPoint, ResultSummary, ResultTab } from "@/types/results";

const API = "/plant-analysis/api";

async function parseJsonResponse<T>(res: Response, fallbackMessage: string): Promise<T> {
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error(
      `${fallbackMessage} (server returned HTML — restart Flask to load latest API routes).`,
    );
  }
  const data = (await res.json()) as T & { error?: string };
  if (!res.ok) {
    throw new Error(data.error || fallbackMessage);
  }
  return data;
}

export async function fetchFilterOptions(): Promise<{
  plants: string[];
  subsystems: string[];
  datasets: string[];
  runs: AnalysisRun[];
  tags: string[];
}> {
  const res = await fetch(`${API}/filters`);
  return parseJsonResponse(res, "Failed to load filter options");
}

export async function fetchSummary(runId: string): Promise<ResultSummary> {
  const res = await fetch(`${API}/results/summary?run_id=${encodeURIComponent(runId)}`);
  return parseJsonResponse(res, "Failed to load summary");
}

export async function fetchSeries(params: {
  runId: string;
  tag?: string;
  dateFrom?: string;
  dateTo?: string;
}): Promise<{ points: ResultPoint[] }> {
  const q = new URLSearchParams({ run_id: params.runId });
  if (params.tag) q.set("tag", params.tag);
  if (params.dateFrom) q.set("date_from", params.dateFrom);
  if (params.dateTo) q.set("date_to", params.dateTo);
  const res = await fetch(`${API}/results/series?${q}`);
  return parseJsonResponse(res, "Failed to load series data");
}

export async function fetchPoints(params: {
  runId: string;
  tab: ResultTab;
  tag?: string;
  dateFrom?: string;
  dateTo?: string;
  severity?: string;
}): Promise<{ points: ResultPoint[]; tags: string[] }> {
  const q = new URLSearchParams({ run_id: params.runId, tab: params.tab });
  if (params.tag) q.set("tag", params.tag);
  if (params.dateFrom) q.set("date_from", params.dateFrom);
  if (params.dateTo) q.set("date_to", params.dateTo);
  if (params.severity) q.set("severity", params.severity);
  const res = await fetch(`${API}/results/points?${q}`);
  return parseJsonResponse(res, "Failed to load result points");
}

export async function runAnalysis(formData: FormData): Promise<{ run_id: string }> {
  const res = await fetch(`${API}/analyze`, { method: "POST", body: formData });
  return parseJsonResponse(res, "Analysis failed");
}

export function downloadUrl(params: {
  runId: string;
  tab: ResultTab;
  format: "csv" | "xlsx" | "pdf";
  tag?: string;
}) {
  const q = new URLSearchParams({
    run_id: params.runId,
    tab: params.tab,
    format: params.format,
  });
  if (params.tag) q.set("tag", params.tag);
  return `${API}/results/download?${q}`;
}
