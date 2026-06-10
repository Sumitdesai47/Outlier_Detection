import type { EChartsOption, SeriesOption } from "echarts";
import { PLOT_MARKER_COLORS, PLOT_MARKER_LABELS } from "@/lib/chart/chartMarkers";
import { parseChartTimestamp } from "@/lib/chart/chartTimestamps";
import { withChartDefaults } from "@/lib/chart/chartDefaults";

const TRACE_MARKER_ALIASES: Record<string, keyof typeof PLOT_MARKER_COLORS> = {
  strong_outlier: "strong_outlier",
  mild_outlier: "strong_outlier",
  sudden_jump: "sudden_jump",
  flagged_unclassified: "flagged_unclassified",
  "Strong outlier": "strong_outlier",
  "Sudden jump": "sudden_jump",
  Flagged: "flagged_unclassified",
};

function resolveTraceMarkerStyle(name: string): { label: string; color?: string } {
  const key = TRACE_MARKER_ALIASES[name];
  if (key) {
    return { label: PLOT_MARKER_LABELS[key], color: PLOT_MARKER_COLORS[key] };
  }
  return { label: name };
}

type PlotlyTrace = {
  type?: string;
  mode?: string;
  name?: string;
  x?: unknown;
  y?: unknown;
  line?: { color?: string; width?: number; dash?: string };
  marker?: { color?: string; size?: number; symbol?: string };
  yaxis?: string;
  opacity?: number;
  customdata?: unknown;
  hovertemplate?: string;
};

type PlotlyLayout = {
  title?: { text?: string } | string;
  xaxis?: { title?: { text?: string } | string };
  yaxis?: { title?: { text?: string } | string };
  yaxis2?: { title?: { text?: string } | string };
  height?: number;
};

type PlotlyBinaryArray = { dtype?: string; bdata?: string };

function stripPlotlyHtml(raw: string): { title: string; subtext?: string } {
  const lines = raw
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return {
    title: lines[0] ?? raw.replace(/<[^>]*>/g, "").trim(),
    subtext: lines.length > 1 ? lines.slice(1).join(" · ") : undefined,
  };
}

function axisTitle(axis?: { title?: { text?: string } | string }): string | undefined {
  if (!axis?.title) return undefined;
  const text = typeof axis.title === "string" ? axis.title : axis.title.text;
  if (!text) return undefined;
  return text.replace(/<[^>]*>/g, "").trim();
}

function decodePlotlyBinaryArray(value: PlotlyBinaryArray): number[] | null {
  if (!value.bdata || !value.dtype) return null;
  try {
    const binary = atob(value.bdata);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const dtype = value.dtype;
    if (dtype === "f8") return Array.from(new Float64Array(bytes.buffer));
    if (dtype === "f4") return Array.from(new Float32Array(bytes.buffer));
    if (dtype === "i4") return Array.from(new Int32Array(bytes.buffer));
    if (dtype === "i2") return Array.from(new Int16Array(bytes.buffer));
    return null;
  } catch {
    return null;
  }
}

function asNumericArray(value: unknown): number[] | null {
  if (Array.isArray(value)) {
    const nums = value.map((v) => Number(v));
    return nums.some((n) => Number.isFinite(n)) ? nums : null;
  }
  if (value && typeof value === "object" && "bdata" in value) {
    return decodePlotlyBinaryArray(value as PlotlyBinaryArray);
  }
  return null;
}

function asTimestampArray(value: unknown): (string | number)[] | null {
  if (Array.isArray(value)) {
    const out: (string | number)[] = [];
    for (const item of value) {
      const ts = parseTs(item);
      if (ts != null) out.push(ts);
    }
    return out.length ? out : null;
  }
  return null;
}

function parseTs(value: unknown): string | number | null {
  if (value == null) return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const parsed = parseChartTimestamp(String(value));
  return parsed ?? String(value);
}

function traceToSeries(trace: PlotlyTrace, yAxisIndex: number): SeriesOption | null {
  const xs = asTimestampArray(trace.x) ?? [];
  const ys = asNumericArray(trace.y) ?? [];
  if (!xs.length || !ys.length) return null;

  const len = Math.min(xs.length, ys.length);
  const mode = String(trace.mode ?? "lines");
  const isLine = mode.includes("lines");
  const isMarker = mode.includes("markers");
  const rawName = trace.name ?? "Series";
  const { label: name, color: markerColor } = resolveTraceMarkerStyle(rawName);

  const custom = Array.isArray(trace.customdata)
    ? trace.customdata
    : asNumericArray(trace.customdata);

  const data: (string | number)[][] = [];
  for (let i = 0; i < len; i++) {
    const ts = xs[i];
    const y = ys[i];
    if (ts == null || y == null || !Number.isFinite(y)) continue;
    const actual = custom?.[i];
    data.push(actual != null && Number.isFinite(actual) ? [ts, y, actual] : [ts, y]);
  }

  if (!data.length) return null;

  const color = markerColor ?? trace.line?.color ?? trace.marker?.color ?? undefined;
  const lineWidth = trace.line?.width ?? 2;

  if (isLine && !isMarker) {
    return {
      name,
      type: "line",
      yAxisIndex,
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      lineStyle: { color, width: lineWidth },
      itemStyle: { color },
      emphasis: { focus: "series" },
      data,
    };
  }

  if (isMarker) {
    return {
      name,
      type: "scatter",
      yAxisIndex,
      symbolSize: trace.marker?.size ?? 9,
      itemStyle: { color, opacity: trace.opacity ?? 1 },
      emphasis: { focus: "series", scale: 1.4 },
      data,
    };
  }

  return {
    name,
    type: "line",
    yAxisIndex,
    showSymbol: true,
    symbolSize: 6,
    lineStyle: { color, width: lineWidth },
    itemStyle: { color },
    data,
  };
}

/** Convert Plotly JSON from the Flask API into an ECharts option. */
export function plotlyJsonToEChartsOption(
  plot: { data?: unknown[]; layout?: Record<string, unknown> },
  opts?: { filename?: string; height?: number },
): EChartsOption {
  const traces = (plot.data ?? []) as PlotlyTrace[];
  const layout = (plot.layout ?? {}) as PlotlyLayout;
  const hasY2 = traces.some((t) => String(t.yaxis ?? "y") === "y2");

  const series: SeriesOption[] = [];
  for (const trace of traces) {
    const yIdx = String(trace.yaxis ?? "y") === "y2" ? 1 : 0;
    const s = traceToSeries(trace, yIdx);
    if (s) series.push(s);
  }

  const rawTitle =
    typeof layout.title === "string" ? layout.title : layout.title?.text ?? "";
  const { title, subtext } = rawTitle ? stripPlotlyHtml(rawTitle) : { title: "" };

  const option: EChartsOption = {
    title: title
      ? {
          text: title,
          subtext,
          left: "center",
          top: 4,
          textStyle: { fontSize: 15, fontWeight: 600, color: "#0f172a" },
          subtextStyle: { fontSize: 12, color: "#64748b" },
        }
      : undefined,
    legend: {
      type: "scroll",
      bottom: 4,
      left: "center",
      textStyle: { color: "#475569", fontSize: 11 },
    },
    grid: {
      left: 64,
      right: hasY2 ? 72 : 28,
      top: subtext ? 72 : 56,
      bottom: 88,
      containLabel: true,
    },
    xAxis: {
      type: "time",
      name: axisTitle(layout.xaxis),
      nameLocation: "middle",
      nameGap: 28,
      axisLine: { lineStyle: { color: "#cbd5e1" } },
      axisLabel: { color: "#64748b", fontSize: 11 },
      splitLine: { show: true, lineStyle: { color: "rgba(148,163,184,0.2)" } },
    },
    yAxis: hasY2
      ? [
          {
            type: "value",
            name: axisTitle(layout.yaxis),
            scale: true,
            axisLine: { show: true, lineStyle: { color: "#cbd5e1" } },
            axisLabel: { color: "#64748b" },
            splitLine: { lineStyle: { color: "rgba(148,163,184,0.2)" } },
          },
          {
            type: "value",
            name: axisTitle(layout.yaxis2),
            scale: true,
            position: "right",
            axisLine: { show: true, lineStyle: { color: "#cbd5e1" } },
            axisLabel: { color: "#64748b" },
            splitLine: { show: false },
          },
        ]
      : {
          type: "value",
          name: axisTitle(layout.yaxis),
          scale: true,
          axisLine: { show: true, lineStyle: { color: "#cbd5e1" } },
          axisLabel: { color: "#64748b" },
          splitLine: { lineStyle: { color: "rgba(148,163,184,0.2)" } },
        },
    series,
  };

  const merged = withChartDefaults(option, {
    filename: opts?.filename,
    showSlider: true,
  });

  return {
    ...merged,
    title: option.title,
    legend: option.legend,
    grid: option.grid,
  };
}
