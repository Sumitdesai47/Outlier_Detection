import { useEffect, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Calendar, LineChart, ListOrdered } from "lucide-react";
import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { plotlyJsonToEChartsOption } from "@/lib/chart/plotlyToECharts";
import { TopBar } from "@/components/layout/TopBar";
import { LoadingState } from "@/components/results/LoadingState";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useLiveResultDashboardStore } from "@/store/liveResultDashboardStore";

export function LiveResultDashboardPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const {
    filters,
    runs,
    overview,
    detail,
    selectedTag,
    compareTags,
    loading,
    plotLoading,
    error,
    setFilter,
    setSelectedDay,
    setSelectedTag,
    toggleCompareTag,
    clearCompareTags,
    loadFilters,
  } = useLiveResultDashboardStore();

  useEffect(() => {
    const plant = searchParams.get("plant") ?? "";
    const area = searchParams.get("area") ?? "";
    const runId = searchParams.get("run_id") ?? "";
    void loadFilters().then(() => {
      if (runId) {
        setFilter({
          runId,
          ...(plant ? { plant } : {}),
          ...(area ? { subsystem: area } : {}),
        });
      } else if (plant && area) {
        setFilter({ plant, subsystem: area });
      }
    });
  }, [loadFilters, searchParams, setFilter]);

  useEffect(() => {
    if (filters.plant && filters.subsystem) {
      const q = new URLSearchParams({ plant: filters.plant, area: filters.subsystem });
      if (filters.runId) q.set("run_id", filters.runId);
      navigate(`/results/live?${q}`, { replace: true });
    }
  }, [filters.plant, filters.subsystem, filters.runId, navigate]);

  const hasRuns = runs.length > 0;

  const liveChartOption = useMemo(() => {
    if (!detail?.plot) return null;
    return plotlyJsonToEChartsOption(detail.plot, {
      filename: `live_outlier_${selectedTag || "tag"}`,
    });
  }, [detail?.plot, selectedTag]);

  return (
    <div>
      <TopBar
        title="Live Outlier Result Dashboard"
        subtitle={
          filters.plant && filters.subsystem
            ? `${filters.plant} · ${filters.subsystem} — same V5 logic as Live Outlier detection`
            : "Select a plant and area from the sidebar (Live Outlier uploads only)."
        }
      />
      <div className="space-y-6 p-6">
        {!hasRuns && !loading ? (
          <Card>
            <CardContent className="py-12 text-center text-sm text-muted-foreground">
              No Live Outlier (V5) results yet. Use{" "}
              <strong>Upload &amp; Configure Live</strong> in the sidebar to upload and analyze data.
            </CardContent>
          </Card>
        ) : null}

        {hasRuns && (!filters.plant || !filters.subsystem) ? (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              Choose a plant and area under <strong>Live Result Dashboard</strong> in the sidebar.
            </CardContent>
          </Card>
        ) : null}

        {loading ? <LoadingState /> : null}
        {error ? (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </div>
        ) : null}

        {!loading && overview && !overview.error ? (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Calendar className="h-4 w-4" />
                <label htmlFor="live-day">UTC day</label>
              </div>
              <input
                id="live-day"
                type="date"
                className="rounded-lg border bg-background px-3 py-2 text-sm"
                value={overview.selected_day ?? ""}
                min={overview.observation_first}
                max={overview.observation_last}
                onChange={(e) => setSelectedDay(e.target.value)}
              />
              <span className="text-xs text-muted-foreground">
                {overview.observation_days.length} observation day
                {overview.observation_days.length === 1 ? "" : "s"} in dataset
              </span>
            </div>

            {!overview.has_outlier_day ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
                No <strong>Strong Anomaly</strong> tags for {overview.selected_day}. Pick another day
                or inspect other anomaly classes in the stored analysis.
              </div>
            ) : null}

            <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
              <Card className="overflow-hidden">
                <CardHeader className="border-b bg-muted/30 py-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <LineChart className="h-4 w-4" />
                    Plot
                    {selectedTag ? (
                      <span className="font-mono text-sm font-normal text-muted-foreground">
                        {selectedTag}
                      </span>
                    ) : null}
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-4">
                  {plotLoading ? (
                    <p className="py-20 text-center text-sm text-muted-foreground">Loading plot…</p>
                  ) : liveChartOption ? (
                    <TimeSeriesChart
                      key={`${selectedTag}|${overview.selected_day}|${compareTags.join(",")}`}
                      option={liveChartOption}
                      height={480}
                      filename={`live_outlier_${selectedTag || "tag"}`}
                      className="border-0 p-0 shadow-none"
                    />
                  ) : (
                    <p className="py-20 text-center text-sm text-muted-foreground">
                      Select a strong anomaly tag to view the chart.
                    </p>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="border-b bg-muted/30 py-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <ListOrdered className="h-4 w-4" />
                    Strong anomaly tags
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  <ul className="max-h-[520px] divide-y overflow-y-auto">
                    {overview.drifts.map((row) => (
                      <li key={row.tag}>
                        <button
                          type="button"
                          onClick={() => setSelectedTag(row.tag)}
                          className={cn(
                            "flex w-full items-center justify-between gap-2 px-4 py-3 text-left text-sm transition-colors hover:bg-muted/50",
                            selectedTag === row.tag && "bg-primary/10 font-medium",
                          )}
                        >
                          <span>
                            <span className="text-muted-foreground">#{row.rank}</span>{" "}
                            <span className="font-mono">{row.tag}</span>
                          </span>
                          <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-800">
                            {row.drift_score}
                          </span>
                        </button>
                      </li>
                    ))}
                    {!overview.drifts.length ? (
                      <li className="px-4 py-6 text-sm text-muted-foreground">No tags listed.</li>
                    ) : null}
                  </ul>
                </CardContent>
              </Card>
            </div>

            {detail?.roots?.length ? (
              <Card>
                <CardHeader className="py-3">
                  <CardTitle className="text-base">Compare tags on plot</CardTitle>
                  <p className="text-sm font-normal text-muted-foreground">
                    Correlated tags by Pearson r (same as Live Outlier detection). Click to toggle
                    compare lines.
                  </p>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex flex-wrap gap-2">
                    {detail.roots.slice(0, 24).map((root) => {
                      const active = compareTags.includes(root.root_cause);
                      return (
                        <Button
                          key={root.root_cause}
                          type="button"
                          variant={active ? "default" : "outline"}
                          className="h-8 font-mono text-xs"
                          onClick={() => toggleCompareTag(root.root_cause)}
                        >
                          {root.root_cause}
                          <span className="ml-1 opacity-70">
                            ({root.root_cause_score.toFixed(2)})
                          </span>
                        </Button>
                      );
                    })}
                  </div>
                  {compareTags.length ? (
                    <Button variant="ghost" className="h-8 text-xs" onClick={clearCompareTags}>
                      Clear compare tags
                    </Button>
                  ) : null}
                </CardContent>
              </Card>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}
