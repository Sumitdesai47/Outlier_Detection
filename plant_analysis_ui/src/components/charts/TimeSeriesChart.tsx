import { useCallback, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { ECharts, EChartsOption } from "echarts";
import {
  Download,
  Maximize2,
  Minimize2,
  MoveHorizontal,
  RotateCcw,
  Table2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { withChartDefaults } from "@/lib/chart/chartDefaults";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface TimeSeriesChartProps {
  option: EChartsOption;
  height?: number;
  filename?: string;
  loading?: boolean;
  empty?: boolean;
  emptyMessage?: string;
  className?: string;
  showToolbar?: boolean;
}

export function TimeSeriesChart({
  option,
  height = 440,
  filename = "plant_analysis_chart",
  loading = false,
  empty = false,
  emptyMessage = "No data to display.",
  className,
  showToolbar = true,
}: TimeSeriesChartProps) {
  const chartRef = useRef<ReactECharts>(null);
  const [showSlider, setShowSlider] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);

  const mergedOption = useMemo(
    () => withChartDefaults(option, { filename, showSlider }),
    [option, filename, showSlider],
  );

  const getChart = useCallback((): ECharts | undefined => {
    return chartRef.current?.getEchartsInstance();
  }, []);

  const restoreView = useCallback(() => {
    getChart()?.dispatchAction({ type: "restore" });
  }, [getChart]);

  const zoomAxis = useCallback(
    (factor: number) => {
      const chart = getChart();
      if (!chart) return;
      const opt = chart.getOption() as { dataZoom?: Array<{ start?: number; end?: number }> };
      const dz = opt.dataZoom?.[0];
      const start = dz?.start ?? 0;
      const end = dz?.end ?? 100;
      const mid = (start + end) / 2;
      const span = (end - start) * factor;
      const nextStart = Math.max(0, mid - span / 2);
      const nextEnd = Math.min(100, mid + span / 2);
      chart.dispatchAction({
        type: "dataZoom",
        start: nextStart,
        end: nextEnd,
      });
    },
    [getChart],
  );

  const savePng = useCallback(() => {
    const chart = getChart();
    if (!chart) return;
    const url = chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#fff" });
    const link = document.createElement("a");
    link.download = `${filename}.png`;
    link.href = url;
    link.click();
  }, [filename, getChart]);

  const openDataView = useCallback(() => {
    getChart()?.dispatchAction({ type: "dataView" });
  }, [getChart]);

  const enableBrushZoom = useCallback(() => {
    getChart()?.dispatchAction({
      type: "takeGlobalCursor",
      key: "brush",
      brushOption: { brushType: "lineX", brushMode: "single" },
    });
  }, [getChart]);

  if (empty) {
    return (
      <div
        className={cn(
          "flex items-center justify-center rounded-lg border bg-muted/20 text-sm text-muted-foreground",
          className,
        )}
        style={{ minHeight: height }}
      >
        {emptyMessage}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "space-y-3 rounded-lg border bg-card p-4",
        fullscreen && "fixed inset-4 z-50 overflow-auto shadow-2xl",
        className,
      )}
    >
      {showToolbar ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            className="h-8 gap-1.5 px-3 text-xs"
            onClick={() => zoomAxis(0.7)}
            title="Zoom in on time axis"
          >
            <ZoomIn className="h-3.5 w-3.5" />
            Zoom in
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-8 gap-1.5 px-3 text-xs"
            onClick={() => zoomAxis(1.35)}
            title="Zoom out on time axis"
          >
            <ZoomOut className="h-3.5 w-3.5" />
            Zoom out
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-8 gap-1.5 px-3 text-xs"
            onClick={enableBrushZoom}
            title="Drag on chart to zoom a time range"
          >
            <MoveHorizontal className="h-3.5 w-3.5" />
            Select range
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-8 gap-1.5 px-3 text-xs"
            onClick={restoreView}
            title="Reset zoom and pan"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Reset
          </Button>
          <Button
            type="button"
            variant={showSlider ? "default" : "outline"}
            className="h-8 px-3 text-xs"
            onClick={() => setShowSlider((v) => !v)}
            title="Toggle bottom time-range slider"
          >
            Slider
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-8 gap-1.5 px-3 text-xs"
            onClick={openDataView}
            title="View underlying data in a table"
          >
            <Table2 className="h-3.5 w-3.5" />
            Data table
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-8 gap-1.5 px-3 text-xs"
            onClick={savePng}
            title="Download chart as PNG"
          >
            <Download className="h-3.5 w-3.5" />
            PNG
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-8 gap-1.5 px-3 text-xs"
            onClick={() => setFullscreen((v) => !v)}
            title={fullscreen ? "Exit fullscreen" : "Expand chart"}
          >
            {fullscreen ? (
              <Minimize2 className="h-3.5 w-3.5" />
            ) : (
              <Maximize2 className="h-3.5 w-3.5" />
            )}
            {fullscreen ? "Exit" : "Expand"}
          </Button>
          <span className="text-xs text-muted-foreground">
            Scroll to zoom · Drag slider · Legend toggles series · Use chart icons (top-right) for
            more tools
          </span>
        </div>
      ) : null}

      <ReactECharts
        ref={chartRef}
        option={mergedOption}
        notMerge
        lazyUpdate
        showLoading={loading}
        style={{ width: "100%", height: fullscreen ? "calc(100vh - 8rem)" : height }}
        opts={{ renderer: "canvas" }}
      />
    </div>
  );
}
