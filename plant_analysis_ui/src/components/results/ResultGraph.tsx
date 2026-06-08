import type { ComponentProps } from "react";
import { useMemo, useState } from "react";
import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import { ZoomIn, Move, RotateCcw, Download } from "lucide-react";
import { sortByTimestampAsc } from "@/lib/sortResults";
import type { ResultPoint, ResultStatus } from "@/types/results";
import { Button } from "@/components/ui/button";
import type { Layout, PlotData } from "plotly.js";

const Plot = createPlotlyComponent(Plotly);

const STATUS_COLORS: Record<string, string> = {
  "Outlier Only": "#dc2626",
  "Process Issue Only": "#d97706",
  Both: "#7c3aed",
};

type DragMode = "zoom" | "pan";

function parseTimestamp(value: string | null): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toISOString();
}

export function ResultGraph({
  seriesPoints,
  highlightPoints,
  selectedTag,
  statusLabel,
}: {
  seriesPoints: ResultPoint[];
  highlightPoints: ResultPoint[];
  selectedTag: string;
  statusLabel: string;
}) {
  const [dragMode, setDragMode] = useState<DragMode>("zoom");
  const [revision, setRevision] = useState(0);

  const lineSource = selectedTag
    ? seriesPoints.filter((p) => p.tag_name === selectedTag)
    : seriesPoints;

  const highlightSource = selectedTag
    ? highlightPoints.filter((p) => p.tag_name === selectedTag)
    : highlightPoints;

  const highlightByTimestamp = useMemo(
    () =>
      new Map(
        sortByTimestampAsc(highlightSource).map((p) => [
          p.observed_at ?? "",
          {
            status: p.status as ResultStatus,
            score: p.outlier_score ?? p.process_issue_score,
            reason: p.reason,
          },
        ]),
      ),
    [highlightSource],
  );

  const sortedSeries = useMemo(() => sortByTimestampAsc(lineSource), [lineSource]);

  const highlightColor = STATUS_COLORS[statusLabel] ?? "#7c3aed";

  const { data, layout } = useMemo(() => {
    const x = sortedSeries.map((p) => parseTimestamp(p.observed_at));
    const y = sortedSeries.map((p) => p.tag_value);

    const highlightX: string[] = [];
    const highlightY: number[] = [];
    const hoverText: string[] = [];

    for (const p of sortedSeries) {
      const hit = highlightByTimestamp.get(p.observed_at ?? "");
      if (!hit || p.tag_value == null) continue;
      const ts = parseTimestamp(p.observed_at);
      if (!ts) continue;
      highlightX.push(ts);
      highlightY.push(p.tag_value);
      hoverText.push(
        [
          `<b>${statusLabel}</b>`,
          `Timestamp: ${p.observed_at}`,
          `Value: ${p.tag_value}`,
          `Status: ${hit.status}`,
          hit.score != null ? `Score: ${hit.score}` : "",
          hit.reason ? `Reason: ${hit.reason}` : "",
        ]
          .filter(Boolean)
          .join("<br>"),
      );
    }

    const traces: Partial<PlotData>[] = [
      {
        type: "scatter",
        mode: "lines",
        name: "Full tag trend",
        x,
        y,
        line: { color: "#2563eb", width: 2 },
        hovertemplate:
          "<b>Full tag trend</b><br>Timestamp: %{x}<br>Value: %{y:.4f}<br>Status: Normal<extra></extra>",
      },
      {
        type: "scatter",
        mode: "markers",
        name: statusLabel,
        x: highlightX,
        y: highlightY,
        marker: {
          color: highlightColor,
          size: 11,
          line: { color: "#ffffff", width: 1.5 },
          symbol: "circle",
        },
        text: hoverText,
        hovertemplate: "%{text}<extra></extra>",
      },
    ];

    const chartLayout: Partial<Layout> = {
      autosize: true,
      height: 420,
      margin: { t: 24, r: 24, b: 72, l: 64 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      hovermode: "x unified",
      dragmode: dragMode,
      showlegend: true,
      legend: { orientation: "h", y: 1.12, x: 0 },
      xaxis: {
        title: { text: "Timestamp" },
        type: "date",
        rangeslider: { visible: true, thickness: 0.08 },
        gridcolor: "rgba(148,163,184,0.25)",
        showspikes: true,
        spikemode: "across",
        spikecolor: "#64748b",
        spikethickness: 1,
      },
      yaxis: {
        title: { text: selectedTag ? `${selectedTag} value` : "Tag value" },
        gridcolor: "rgba(148,163,184,0.25)",
        zeroline: false,
        fixedrange: false,
      },
    };

    return { data: traces, layout: chartLayout };
  }, [sortedSeries, highlightByTimestamp, highlightColor, statusLabel, selectedTag, dragMode]);

  if (!sortedSeries.length) {
    return (
      <div className="flex h-72 items-center justify-center rounded-lg border bg-muted/20 text-sm text-muted-foreground">
        Select a tag to view the full time series with highlighted findings.
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={dragMode === "zoom" ? "default" : "outline"}
          className="h-8 gap-1.5 px-3 text-xs"
          onClick={() => setDragMode("zoom")}
        >
          <ZoomIn className="h-3.5 w-3.5" />
          Box zoom
        </Button>
        <Button
          variant={dragMode === "pan" ? "default" : "outline"}
          className="h-8 gap-1.5 px-3 text-xs"
          onClick={() => setDragMode("pan")}
        >
          <Move className="h-3.5 w-3.5" />
          Pan
        </Button>
        <Button
          variant="outline"
          className="h-8 gap-1.5 px-3 text-xs"
          onClick={() => setRevision((n) => n + 1)}
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Reset view
        </Button>
        <span className="text-xs text-muted-foreground">
          Scroll to zoom · Double-click to reset · Use range slider below chart
        </span>
      </div>

      <Plot
        data={data}
        layout={layout}
        revision={revision}
        useResizeHandler
        style={{ width: "100%", minHeight: 420 }}
        config={{
          scrollZoom: true,
          responsive: true,
          displaylogo: false,
          modeBarButtonsToRemove: ["lasso2d", "select2d"],
          toImageButtonOptions: {
            format: "png",
            filename: `plant_analysis_${selectedTag || "tag"}_chart`,
            scale: 2,
          },
          modeBarButtonsToAdd: ["zoomIn2d", "zoomOut2d", "autoScale2d", "resetScale2d"],
        }}
      />

      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Download className="h-3.5 w-3.5" />
        Use the camera icon on the chart to download PNG. Toolbar supports zoom in, zoom out, autoscale, and reset.
      </p>
    </div>
  );
}

export function OutlierGraph(
  props: Omit<ComponentProps<typeof ResultGraph>, "statusLabel">,
) {
  return <ResultGraph {...props} statusLabel="Outlier Only" />;
}

export function ProcessIssueGraph(
  props: Omit<ComponentProps<typeof ResultGraph>, "statusLabel">,
) {
  return <ResultGraph {...props} statusLabel="Process Issue Only" />;
}

export function BothIssueGraph(
  props: Omit<ComponentProps<typeof ResultGraph>, "statusLabel">,
) {
  return <ResultGraph {...props} statusLabel="Both" />;
}
