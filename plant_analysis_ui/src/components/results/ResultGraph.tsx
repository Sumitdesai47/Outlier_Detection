import type { ComponentProps } from "react";
import { useEffect, useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import type { EChartsOption, SeriesOption } from "echarts";
import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import {
  PLOT_MARKER_ORDER,
  type PlotMarkerKey,
  plotMarkerColor,
  plotMarkerLabel,
  resolvePlotMarkerKey,
} from "@/lib/chart/chartMarkers";
import { issueCategory, issueCategoryLabel } from "@/lib/issueClassification";
import { markerTypeLabel, s5PeerLabel } from "@/lib/resultTableFormat";
import { sortByTimestampAsc } from "@/lib/sortResults";
import { chartTimestampKey, parseChartTimestamp } from "@/lib/chart/chartTimestamps";
import type { ResultPoint } from "@/types/results";

const COMPARE_LINE_COLORS = ["#16a34a", "#a855f7", "#0d9488", "#ca8a04", "#7c3aed", "#ea580c"];

type HighlightMeta = {
  point: ResultPoint;
  markerKey: PlotMarkerKey;
};

type MarkerDatum = {
  value: [string, number];
  itemStyle: { color: string };
  tooltip: { formatter: string };
};

type LineDatum = {
  value: [string, number];
  actual: number;
};

type ValueBounds = { min: number; max: number };

function valueBounds(points: ResultPoint[]): ValueBounds | null {
  const values: number[] = [];
  for (const p of points) {
    if (p.tag_value != null && Number.isFinite(p.tag_value)) values.push(p.tag_value);
  }
  if (!values.length) return null;
  return { min: Math.min(...values), max: Math.max(...values) };
}

function normalizeValue(value: number, bounds: ValueBounds): number {
  if (bounds.max === bounds.min) return 0.5;
  return (value - bounds.min) / (bounds.max - bounds.min);
}

function formatActualValue(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1000 || (abs > 0 && abs < 0.01)) return value.toExponential(4);
  return Number(value.toFixed(4)).toString();
}

function seriesLineData(points: ResultPoint[]): LineDatum[] {
  const out: LineDatum[] = [];
  for (const p of sortByTimestampAsc(points)) {
    const ts = parseChartTimestamp(p.observed_at);
    if (!ts || p.tag_value == null) continue;
    out.push({ value: [ts, p.tag_value], actual: p.tag_value });
  }
  return out;
}

function seriesNormalizedLineData(points: ResultPoint[]): LineDatum[] {
  const bounds = valueBounds(points);
  if (!bounds) return [];
  const out: LineDatum[] = [];
  for (const p of sortByTimestampAsc(points)) {
    const ts = parseChartTimestamp(p.observed_at);
    if (!ts || p.tag_value == null) continue;
    out.push({
      value: [ts, normalizeValue(p.tag_value, bounds)],
      actual: p.tag_value,
    });
  }
  return out;
}

function isSeriesHidden(name: string, hiddenSeries: Set<string>): boolean {
  return hiddenSeries.has(name);
}

function buildChartOption(
  primarySeries: ResultPoint[],
  compareSeriesByTag: Map<string, ResultPoint[]>,
  highlightByTimestamp: Map<string, HighlightMeta>,
  selectedTag: string,
  hiddenSeries: Set<string>,
): EChartsOption {
  const normalizeCompare = compareSeriesByTag.size > 0;
  const primaryBounds = normalizeCompare ? valueBounds(primarySeries) : null;

  const markerSeriesByType = new Map<PlotMarkerKey, MarkerDatum[]>();
  for (const key of PLOT_MARKER_ORDER) markerSeriesByType.set(key, []);

  for (const p of sortByTimestampAsc(primarySeries)) {
    const hit = highlightByTimestamp.get(chartTimestampKey(p.observed_at));
    if (!hit || p.tag_value == null) continue;
    const ts = parseChartTimestamp(p.observed_at);
    if (!ts) continue;

    const { point, markerKey } = hit;
    const score = point.outlier_score ?? point.process_issue_score;
    const cat = issueCategory(point);
    const lines = [
      `<b>${plotMarkerLabel(markerKey)}</b>`,
      cat ? `Issue: ${issueCategoryLabel(cat)}` : "",
      `S5: ${s5PeerLabel(point)}`,
      `Timestamp: ${p.observed_at}`,
      `Actual: ${formatActualValue(p.tag_value)}`,
      point.final_class ? `Class: ${point.final_class}` : `Marker: ${markerTypeLabel(point)}`,
      score != null ? `Score: ${score}` : "",
    ].filter(Boolean);

    const markerY =
      normalizeCompare && primaryBounds
        ? normalizeValue(p.tag_value, primaryBounds)
        : p.tag_value;

    markerSeriesByType.get(markerKey)?.push({
      value: [ts, markerY],
      itemStyle: { color: plotMarkerColor(markerKey) },
      tooltip: { formatter: lines.join("<br/>") },
    });
  }

  const markerSeries: SeriesOption[] = PLOT_MARKER_ORDER.flatMap((key) => {
    const label = plotMarkerLabel(key);
    const data = markerSeriesByType.get(key) ?? [];
    if (!data.length || isSeriesHidden(label, hiddenSeries)) return [];
    return [
      {
        name: label,
        type: "scatter",
        symbolSize: 9,
        itemStyle: { color: plotMarkerColor(key), borderColor: "#fff", borderWidth: 1 },
        emphasis: { scale: 1.35, focus: "series" },
        data,
        tooltip: { trigger: "item" },
      },
    ];
  });

  const toLineData = normalizeCompare ? seriesNormalizedLineData : seriesLineData;

  const primaryName = selectedTag || "Primary tag";
  const lineSeries: SeriesOption[] = [
    {
      name: primaryName,
      type: "line",
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      lineStyle: { color: "#2563eb", width: 2.5 },
      itemStyle: { color: "#2563eb" },
      emphasis: { focus: "series" },
      data: isSeriesHidden(primaryName, hiddenSeries) ? [] : toLineData(primarySeries),
    },
  ];

  let colorIdx = 0;
  for (const [tag, pts] of compareSeriesByTag) {
    const color = COMPARE_LINE_COLORS[colorIdx % COMPARE_LINE_COLORS.length];
    colorIdx += 1;
    lineSeries.push({
      name: tag,
      type: "line",
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      lineStyle: { color, width: 1.8, type: "dashed" },
      itemStyle: { color },
      emphasis: { focus: "series" },
      data: isSeriesHidden(tag, hiddenSeries) ? [] : toLineData(pts),
    });
  }

  return {
    legend: { show: false },
    grid: {
      left: 56,
      right: 28,
      top: 24,
      bottom: 72,
      containLabel: true,
    },
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const items = (Array.isArray(params) ? params : [params]) as Array<{
          axisValueLabel?: string;
          marker?: string;
          seriesName?: string;
          data?: LineDatum;
          value?: [string, number];
        }>;
        if (!items.length) return "";
        const header = items[0].axisValueLabel ?? items[0].value?.[0] ?? "";
        const markerLabels = new Set(PLOT_MARKER_ORDER.map((key) => plotMarkerLabel(key)));
        const lines = items
          .filter((item) => item.seriesName && !markerLabels.has(item.seriesName))
          .map((item) => {
            const actual = item.data?.actual ?? item.value?.[1];
            if (actual == null || !Number.isFinite(actual)) return "";
            const label = normalizeCompare ? "Actual" : "Value";
            return `${item.marker ?? ""}${item.seriesName}: ${label}: ${formatActualValue(actual)}`;
          })
          .filter(Boolean);
        return [header, ...lines].join("<br/>");
      },
    },
    xAxis: {
      type: "time",
      name: "Timestamp",
      nameLocation: "middle",
      nameGap: 30,
    },
    yAxis: {
      type: "value",
      name: normalizeCompare ? "Normalized (0–1)" : selectedTag ? `${selectedTag} value` : "Tag value",
      min: normalizeCompare ? 0 : undefined,
      max: normalizeCompare ? 1 : undefined,
      scale: !normalizeCompare,
    },
    series: [...lineSeries, ...markerSeries],
  };
}

type LegendItem = {
  key: string;
  label: string;
  kind: "primary" | "compare" | "marker";
  color?: string;
  markerKey?: PlotMarkerKey;
  compareIdx?: number;
};

function ChartBottomLegend({
  items,
  hiddenSeries,
  onToggle,
}: {
  items: LegendItem[];
  hiddenSeries: Set<string>;
  onToggle: (key: string) => void;
}) {
  return (
    <div className="rounded-md border bg-muted/15 px-3 py-3">
      <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        Chart legend — click to show or hide
      </p>
      <div className="flex flex-wrap gap-2">
        {items.map((item) => {
          const hidden = hiddenSeries.has(item.key);
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onToggle(item.key)}
              className={cn(
                "inline-flex items-center gap-2 rounded-md border bg-background px-3 py-1.5 text-xs transition-colors hover:bg-muted",
                hidden && "opacity-50",
              )}
              aria-pressed={!hidden}
            >
              {item.kind === "primary" ? (
                <span className="inline-block h-0.5 w-6 rounded bg-[#2563eb]" aria-hidden />
              ) : null}
              {item.kind === "compare" ? (
                <span
                  className="inline-block h-0 w-6 border-t-[3px] border-dashed"
                  style={{
                    borderColor:
                      COMPARE_LINE_COLORS[(item.compareIdx ?? 0) % COMPARE_LINE_COLORS.length],
                  }}
                  aria-hidden
                />
              ) : null}
              {item.kind === "marker" && item.markerKey ? (
                <span
                  className="inline-block h-2.5 w-2.5 rounded-full border border-white shadow-sm"
                  style={{ backgroundColor: plotMarkerColor(item.markerKey) }}
                  aria-hidden
                />
              ) : null}
              <span className={cn("text-foreground", hidden && "line-through")}>{item.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function ResultGraph({
  seriesPoints,
  highlightPoints,
  selectedTag,
  compareTags = [],
}: {
  seriesPoints: ResultPoint[];
  highlightPoints: ResultPoint[];
  selectedTag: string;
  compareTags?: string[];
  statusLabel?: string;
}) {
  const primarySeries = useMemo(
    () =>
      selectedTag
        ? seriesPoints.filter((p) => p.tag_name === selectedTag)
        : seriesPoints,
    [seriesPoints, selectedTag],
  );

  const compareSeriesByTag = useMemo(() => {
    const map = new Map<string, ResultPoint[]>();
    for (const tag of compareTags) {
      if (!tag || tag === selectedTag) continue;
      const pts = seriesPoints.filter((p) => p.tag_name === tag);
      if (pts.length) map.set(tag, pts);
    }
    return map;
  }, [seriesPoints, compareTags, selectedTag]);

  const highlightByTimestamp = useMemo(() => {
    const source = selectedTag
      ? highlightPoints.filter((p) => p.tag_name === selectedTag)
      : highlightPoints;
    const map = new Map<string, HighlightMeta>();
    for (const p of sortByTimestampAsc(source)) {
      const markerKey = resolvePlotMarkerKey(p);
      if (!markerKey) continue;
      map.set(chartTimestampKey(p.observed_at), { point: p, markerKey });
    }
    return map;
  }, [highlightPoints, selectedTag]);

  const activeMarkerKeys = useMemo(() => {
    const keys = new Set<PlotMarkerKey>();
    for (const { markerKey } of highlightByTimestamp.values()) keys.add(markerKey);
    return PLOT_MARKER_ORDER.filter((key) => keys.has(key));
  }, [highlightByTimestamp]);

  const compareList = compareTags.filter((tag) => tag && tag !== selectedTag);

  const [hiddenSeries, setHiddenSeries] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    setHiddenSeries(new Set());
  }, [selectedTag, compareList.join("|"), activeMarkerKeys.join("|")]);

  const legendItems = useMemo((): LegendItem[] => {
    const items: LegendItem[] = [
      {
        key: selectedTag || "Primary tag",
        label: selectedTag || "Primary tag",
        kind: "primary",
      },
    ];
    compareList.forEach((tag, idx) => {
      items.push({ key: tag, label: tag, kind: "compare", compareIdx: idx });
    });
    for (const markerKey of activeMarkerKeys) {
      const label = plotMarkerLabel(markerKey);
      items.push({ key: label, label, kind: "marker", markerKey });
    }
    return items;
  }, [selectedTag, compareList, activeMarkerKeys]);

  const toggleSeries = (key: string) => {
    setHiddenSeries((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const option = useMemo(
    () =>
      buildChartOption(
        primarySeries,
        compareSeriesByTag,
        highlightByTimestamp,
        selectedTag,
        hiddenSeries,
      ),
    [primarySeries, compareSeriesByTag, highlightByTimestamp, selectedTag, hiddenSeries],
  );

  return (
    <div className="space-y-3">
      <TimeSeriesChart
        key={`${selectedTag}|${compareList.join(",")}|${activeMarkerKeys.join(",")}`}
        option={option}
        filename={`plant_analysis_${selectedTag || "tag"}_chart`}
        empty={!primarySeries.length}
        emptyMessage="Select a tag to view the full time series with highlighted findings."
        showToolbar
      />
      {primarySeries.length ? (
        <ChartBottomLegend
          items={legendItems}
          hiddenSeries={hiddenSeries}
          onToggle={toggleSeries}
        />
      ) : null}
    </div>
  );
}

export function OutlierGraph(props: Omit<ComponentProps<typeof ResultGraph>, "statusLabel">) {
  return <ResultGraph {...props} />;
}

export function ProcessIssueGraph(props: Omit<ComponentProps<typeof ResultGraph>, "statusLabel">) {
  return <ResultGraph {...props} />;
}

export function BothIssueGraph(props: Omit<ComponentProps<typeof ResultGraph>, "statusLabel">) {
  return <ResultGraph {...props} />;
}
