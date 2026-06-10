import type { ResultPoint } from "@/types/results";

/** Same colors as services/plant_analysis_plot.py and multimodel tab. */
export const PLOT_MARKER_COLORS = {
  strong_outlier: "#d61f26",
  sudden_jump: "#ea580c",
  flagged_unclassified: "#64748b",
} as const;

export const PLOT_MARKER_LABELS = {
  strong_outlier: "Strong outlier",
  sudden_jump: "Sudden jump",
  flagged_unclassified: "Flagged",
} as const;

export type PlotMarkerKey = keyof typeof PLOT_MARKER_COLORS;

export const PLOT_MARKER_ORDER: PlotMarkerKey[] = [
  "strong_outlier",
  "sudden_jump",
  "flagged_unclassified",
];

/** Mirror services/plant_analysis_multimodel_runner.final_class_to_plot_status */
export function finalClassToPlotStatus(
  finalClass: string,
  finalStatus?: string | null,
): string {
  const fc = finalClass.trim();
  const fs = String(finalStatus ?? "")
    .trim()
    .toLowerCase();
  if (!fc || fc === "Normal" || fc === "Spike - Returned Normal") return "normal";
  if (fc === "Strong Anomaly") return "strong_outlier";
  if (fc === "Drift") return "sudden_jump";
  if (fc === "Contextual Anomaly" || fc === "Drift + Anomaly" || fc === "Anomaly") {
    return "mild_outlier";
  }
  if (fs.includes("jump") || fs.includes("drift")) return "sudden_jump";
  return "flagged_unclassified";
}

/** Mirror services/plant_analysis_plot._collapse_plot_status */
function collapsePlotStatus(plotStatus: string): PlotMarkerKey | null {
  const ps = plotStatus.trim().toLowerCase();
  if (!ps || ps === "normal" || ps === "missing" || ps === "process_issue") return null;
  if (ps === "sudden_jump") return "sudden_jump";
  if (ps === "flagged_unclassified") return "flagged_unclassified";
  return "strong_outlier";
}

/** Mirror services/plant_analysis_plot._resolve_plot_status + collapse. */
export function resolvePlotMarkerKey(point: ResultPoint): PlotMarkerKey | null {
  const plotStatus = String(point.plot_status ?? "").trim();
  if (plotStatus && plotStatus !== "normal") {
    return collapsePlotStatus(plotStatus);
  }

  const finalClass = String(point.final_class ?? "").trim();
  const finalStatus = point.final_status;
  if (finalClass || finalStatus) {
    return collapsePlotStatus(finalClassToPlotStatus(finalClass || "Normal", finalStatus));
  }

  const legacy = inferLegacyPlotMarkerKey(point);
  if (legacy) return legacy;

  if (point.status !== "Normal") return "flagged_unclassified";
  return null;
}

/** Match services/plant_analysis_results_store._infer_legacy_plot_status */
function inferLegacyPlotMarkerKey(point: ResultPoint): PlotMarkerKey | null {
  if (point.plot_status || point.final_class) return null;
  if (point.status === "Normal") return null;
  const outlierScore = point.outlier_score ?? 0;
  const processScore = point.process_issue_score ?? 0;
  if (outlierScore >= 3.5) return "strong_outlier";
  if (processScore >= 2.5) return "sudden_jump";
  return "strong_outlier";
}

export function plotMarkerLabel(key: PlotMarkerKey): string {
  return PLOT_MARKER_LABELS[key];
}

export function plotMarkerColor(key: PlotMarkerKey): string {
  return PLOT_MARKER_COLORS[key];
}
