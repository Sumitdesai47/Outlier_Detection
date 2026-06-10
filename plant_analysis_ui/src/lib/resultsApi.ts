import type {
  AnalysisRun,
  ResultPoint,
  ResultSummary,
  ResultTab,
  RunDayMeta,
  TagContext,
} from "@/types/results";

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

export async function fetchRuns(params?: {
  plant?: string;
  subsystem?: string;
  dataset?: string;
  engine?: string;
}): Promise<AnalysisRun[]> {
  const q = new URLSearchParams();
  if (params?.plant) q.set("plant", params.plant);
  if (params?.subsystem) q.set("subsystem", params.subsystem);
  if (params?.dataset) q.set("dataset", params.dataset);
  if (params?.engine) q.set("engine", params.engine);
  const res = await fetch(`${API}/runs${q.size ? `?${q}` : ""}`);
  const data = await parseJsonResponse<{ runs: AnalysisRun[] }>(res, "Failed to load runs");
  return data.runs;
}

export async function fetchSummary(runId: string): Promise<ResultSummary> {
  const res = await fetch(`${API}/results/summary?run_id=${encodeURIComponent(runId)}`);
  return parseJsonResponse(res, "Failed to load summary");
}

export async function fetchObservationDays(runId: string): Promise<RunDayMeta> {
  const res = await fetch(`${API}/results/days?run_id=${encodeURIComponent(runId)}`);
  return parseJsonResponse(res, "Failed to load observation days");
}

export async function fetchSeries(params: {
  runId: string;
  tag?: string;
  compare?: string[];
  dateFrom?: string;
  dateTo?: string;
}): Promise<{ points: ResultPoint[] }> {
  const q = new URLSearchParams({ run_id: params.runId });
  if (params.tag) q.set("tag", params.tag);
  for (const c of params.compare ?? []) q.append("compare", c);
  if (params.dateFrom) q.set("date_from", params.dateFrom);
  if (params.dateTo) q.set("date_to", params.dateTo);
  const res = await fetch(`${API}/results/series?${q}`);
  return parseJsonResponse(res, "Failed to load series data");
}

export async function fetchTagContext(runId: string, tag: string): Promise<TagContext> {
  const q = new URLSearchParams({ run_id: runId, tag });
  const res = await fetch(`${API}/results/tag-context?${q}`);
  return parseJsonResponse(res, "Failed to load tag context");
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

export function buildQuickAnalysisFormData(params: {
  plantName: string;
  area: string;
  file: File;
  datasetName?: string;
  engine?: "multimodel_outlier" | "live_outlier";
  timestampColumn?: string;
}): FormData {
  const engine = params.engine ?? "multimodel_outlier";
  const formData = new FormData();
  formData.set("plant_name", params.plantName.trim());
  formData.set("subsystem", params.area.trim());
  formData.set("dataset_name", (params.datasetName || params.file.name).trim());
  formData.set("file", params.file);
  formData.set("engine", engine);
  formData.set(
    "config_json",
    JSON.stringify({
      rolling: false,
      duration: engine === "live_outlier" ? "full" : "6m",
      direction: "both",
      minMaxFilters: [],
      tagConditions: [],
      critical_tags: [],
      timestampColumn: params.timestampColumn?.trim() || undefined,
      engine,
    }),
  );
  return formData;
}

export async function deletePlantRuns(plantName: string): Promise<void> {
  const res = await fetch(`${API}/plants/${encodeURIComponent(plantName)}`, {
    method: "DELETE",
  });
  await parseJsonResponse(res, "Failed to delete plant runs");
}

export async function deleteAreaRuns(plantName: string, area: string): Promise<void> {
  const res = await fetch(
    `${API}/areas/${encodeURIComponent(plantName)}/${encodeURIComponent(area)}`,
    {
      method: "DELETE",
    },
  );
  await parseJsonResponse(res, "Failed to delete area runs");
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
