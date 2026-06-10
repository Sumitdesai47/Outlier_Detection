import type { AnalysisRun } from "@/types/results";
import type { LiveDashboardOverview, LiveTagDetail } from "@/types/liveResults";

const API = "/plant-analysis/api";

async function parseJson<T>(res: Response, fallback: string): Promise<T> {
  const data = (await res.json()) as T & { error?: string };
  if (!res.ok) throw new Error(data.error || fallback);
  return data;
}

export async function fetchLiveRuns(): Promise<AnalysisRun[]> {
  const res = await fetch(`${API}/runs?engine=live_outlier`);
  const data = await parseJson<{ runs: AnalysisRun[] }>(res, "Failed to load live runs");
  return data.runs;
}

export async function fetchLiveOverview(params: {
  runId: string;
  day?: string;
}): Promise<LiveDashboardOverview> {
  const q = new URLSearchParams({ run_id: params.runId });
  if (params.day) q.set("day", params.day);
  const res = await fetch(`${API}/results/live?${q}`);
  return parseJson(res, "Failed to load live dashboard");
}

export async function fetchLiveTagDetail(params: {
  runId: string;
  day: string;
  tag: string;
  compare?: string[];
}): Promise<LiveTagDetail> {
  const q = new URLSearchParams({
    run_id: params.runId,
    day: params.day,
    tag: params.tag,
  });
  for (const c of params.compare ?? []) q.append("compare", c);
  const res = await fetch(`${API}/results/live/detail?${q}`);
  return parseJson(res, "Failed to load tag detail");
}
