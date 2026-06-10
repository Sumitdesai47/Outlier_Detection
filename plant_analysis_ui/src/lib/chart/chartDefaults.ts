import type { DataZoomComponentOption, EChartsOption } from "echarts";

export const CHART_FONT =
  "Segoe UI, system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif";

export const CHART_GRID = {
  left: 56,
  right: 28,
  top: 56,
  bottom: 72,
  containLabel: true,
};

/** Merge user option with interactive defaults (zoom, toolbox, crosshair tooltip). */
export function withChartDefaults(
  option: EChartsOption,
  opts?: { filename?: string; showSlider?: boolean },
): EChartsOption {
  const filename = opts?.filename ?? "plant_analysis_chart";
  const showSlider = opts?.showSlider ?? true;

  const sliderZoom: DataZoomComponentOption = {
    type: "slider",
    xAxisIndex: 0,
    height: 22,
    bottom: 10,
    borderColor: "#e2e8f0",
    fillerColor: "rgba(37, 99, 235, 0.12)",
    handleStyle: { color: "#2563eb" },
    textStyle: { color: "#64748b", fontSize: 11 },
    filterMode: "none",
  };

  const dataZoom: EChartsOption["dataZoom"] = [
    { type: "inside", xAxisIndex: 0, filterMode: "none" },
    ...(showSlider ? [sliderZoom] : []),
  ];

  const toolbox: EChartsOption["toolbox"] = {
    orient: "horizontal",
    right: 12,
    top: 8,
    itemSize: 14,
    itemGap: 10,
    showTitle: true,
    feature: {
      dataZoom: {
        yAxisIndex: "none" as const,
        title: { zoom: "Box zoom", back: "Zoom reset" },
      },
      restore: { title: "Restore" },
      saveAsImage: {
        title: "Save PNG",
        name: filename,
        pixelRatio: 2,
        backgroundColor: "#ffffff",
      },
      dataView: {
        title: "Data table",
        readOnly: true,
        lang: ["Data table", "Close", "Refresh"],
      },
      brush: {
        type: ["lineX", "clear"] as ("lineX" | "clear")[],
        title: { lineX: "Select range", clear: "Clear selection" },
      },
    },
  };

  const base: EChartsOption = {
    textStyle: { fontFamily: CHART_FONT },
    animation: true,
    animationDuration: 280,
    grid: CHART_GRID,
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "cross",
        crossStyle: { color: "#94a3b8" },
        lineStyle: { color: "#94a3b8", type: "dashed" },
      },
      backgroundColor: "rgba(255,255,255,0.96)",
      borderColor: "#e2e8f0",
      borderWidth: 1,
      textStyle: { color: "#0f172a", fontSize: 12 },
      confine: true,
    },
    legend: {
      type: "scroll",
      top: 4,
      left: 8,
      textStyle: { color: "#475569", fontSize: 12 },
    },
    toolbox,
    dataZoom,
    brush: {
      toolbox: ["lineX", "clear"],
      xAxisIndex: 0,
      brushStyle: { borderWidth: 1, color: "rgba(37,99,235,0.08)", borderColor: "#2563eb" },
    },
  };

  return deepMerge(base, option);
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function deepMerge<T extends Record<string, unknown>>(base: T, patch: Record<string, unknown>): T {
  const out = { ...base };
  for (const [key, val] of Object.entries(patch)) {
    if (val === undefined) continue;
    const prev = out[key];
    if (isPlainObject(prev) && isPlainObject(val)) {
      out[key as keyof T] = deepMerge(prev, val) as T[keyof T];
    } else {
      out[key as keyof T] = val as T[keyof T];
    }
  }
  return out;
}
