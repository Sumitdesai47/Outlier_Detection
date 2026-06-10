import { buildQuickAnalysisFormData, runAnalysis } from "@/lib/resultsApi";

export async function runLiveOutlierUpload(params: {
  plantName: string;
  area: string;
  file: File;
  datasetName?: string;
  timestampColumn?: string;
}): Promise<{ run_id: string }> {
  const plantName = params.plantName.trim();
  const area = params.area.trim();
  if (!plantName || !area) {
    throw new Error("Plant and area are required.");
  }
  const lower = params.file.name.toLowerCase();
  if (!lower.endsWith(".xlsx") && !lower.endsWith(".xls") && !lower.endsWith(".csv")) {
    throw new Error("Upload .xlsx, .xls, or .csv only.");
  }

  const formData = buildQuickAnalysisFormData({
    plantName,
    area,
    file: params.file,
    engine: "live_outlier",
    timestampColumn: params.timestampColumn,
  });
  if (params.datasetName?.trim()) {
    formData.set("dataset_name", params.datasetName.trim());
  }
  return runAnalysis(formData);
}

export function liveOutlierResultsUrl(params: {
  plantName: string;
  area: string;
  runId: string;
}): string {
  const q = new URLSearchParams({
    plant: params.plantName,
    area: params.area,
    run_id: params.runId,
  });
  return `/results/live?${q}`;
}
