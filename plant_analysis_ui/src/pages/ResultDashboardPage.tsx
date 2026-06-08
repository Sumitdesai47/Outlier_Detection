import { useEffect, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { TopBar } from "@/components/layout/TopBar";
import { DownloadButton } from "@/components/results/DownloadButton";
import { EmptyState } from "@/components/results/EmptyState";
import { LoadingState } from "@/components/results/LoadingState";
import { BothIssueGraph, OutlierGraph, ProcessIssueGraph } from "@/components/results/ResultGraph";
import { ResultTable } from "@/components/results/ResultTable";
import { ResultTabs } from "@/components/results/ResultTabs";
import { SummaryCards } from "@/components/results/SummaryCards";
import { TagDropdown } from "@/components/results/TagDropdown";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  tagCombinedAbnormal,
  tagOutlierExclusive,
  tagProcessExclusive,
} from "@/lib/summaryMetrics";
import { useResultDashboardStore } from "@/store/resultDashboardStore";

export function ResultDashboardPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const {
    activeTab,
    filters,
    runs,
    availableTags,
    summary,
    points,
    seriesPoints,
    loading,
    error,
    setActiveTab,
    setFilter,
    loadFilters,
  } = useResultDashboardStore();

  useEffect(() => {
    const plant = searchParams.get("plant") ?? "";
    const area = searchParams.get("area") ?? "";
    const runId = searchParams.get("run_id") ?? "";

    void loadFilters().then(() => {
      if (plant && area) {
        setFilter({ plant, subsystem: area });
      } else if (runId) {
        setFilter({ runId });
      }
    });
  }, [loadFilters, searchParams, setFilter]);

  useEffect(() => {
    const plant = searchParams.get("plant");
    const area = searchParams.get("area");
    if (filters.plant && filters.subsystem && (!plant || !area)) {
      const q = new URLSearchParams({
        plant: filters.plant,
        area: filters.subsystem,
      });
      if (filters.runId) q.set("run_id", filters.runId);
      navigate(`/results?${q}`, { replace: true });
    }
  }, [filters.plant, filters.subsystem, filters.runId, navigate, searchParams]);

  const hasRuns = runs.length > 0;

  return (
    <div>
      <TopBar
        title="Result Dashboard"
        subtitle={
          filters.plant && filters.subsystem
            ? `${filters.plant} · ${filters.subsystem}`
            : "Select a plant and area from the sidebar to view results."
        }
      />
      <div className="space-y-6 p-6">
        {!hasRuns && !loading ? <EmptyState /> : null}

        {hasRuns && (!filters.plant || !filters.subsystem) && !loading ? (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              Choose a plant and area under <strong>Result Dashboard</strong> in the sidebar.
            </CardContent>
          </Card>
        ) : null}

        {hasRuns && filters.plant && filters.subsystem ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <ResultTabs activeTab={activeTab} onChange={setActiveTab} />
              <DownloadButton runId={filters.runId} tab={activeTab} tag={filters.tag || undefined} />
            </div>

            {loading ? <LoadingState /> : null}
            {error ? (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
                {error}
              </div>
            ) : null}

            {!loading && summary && activeTab === "summary" ? (
              <>
                <SummaryCards summary={summary} />
                <Card>
                  <CardHeader>
                    <CardTitle>Tag-wise summary</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="overflow-x-auto rounded-lg border">
                      <table className="min-w-full text-left text-sm">
                        <thead className="bg-muted/50">
                          <tr>
                            <th className="px-3 py-2">Tag</th>
                            <th className="px-3 py-2">Total</th>
                            <th className="px-3 py-2">Outlier</th>
                            <th className="px-3 py-2">Process</th>
                            <th className="px-3 py-2">Both</th>
                            <th className="px-3 py-2">Normal</th>
                          </tr>
                        </thead>
                        <tbody>
                          {summary.tag_summaries.map((row) => (
                            <tr key={row.tag_name} className="border-t">
                              <td className="px-3 py-2">{row.tag_name}</td>
                              <td className="px-3 py-2">{row.total_points}</td>
                              <td className="px-3 py-2">{tagOutlierExclusive(row)}</td>
                              <td className="px-3 py-2">{tagProcessExclusive(row)}</td>
                              <td className="px-3 py-2">{tagCombinedAbnormal(row)}</td>
                              <td className="px-3 py-2">{row.normal}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </CardContent>
                </Card>
              </>
            ) : null}

            {!loading && activeTab === "outlier" ? (
              <TabPanel
                graph={
                  <OutlierGraph
                    seriesPoints={seriesPoints}
                    highlightPoints={points}
                    selectedTag={filters.tag}
                  />
                }
                table={<ResultTable tab="outlier" points={points} />}
                tagSelector={
                  <TagDropdown
                    value={filters.tag}
                    tags={availableTags}
                    onChange={(tag) => setFilter({ tag })}
                  />
                }
              />
            ) : null}

            {!loading && activeTab === "process" ? (
              <TabPanel
                graph={
                  <ProcessIssueGraph
                    seriesPoints={seriesPoints}
                    highlightPoints={points}
                    selectedTag={filters.tag}
                  />
                }
                table={<ResultTable tab="process" points={points} />}
                tagSelector={
                  <TagDropdown
                    value={filters.tag}
                    tags={availableTags}
                    onChange={(tag) => setFilter({ tag })}
                  />
                }
              />
            ) : null}

            {!loading && activeTab === "both" ? (
              <TabPanel
                graph={
                  <BothIssueGraph
                    seriesPoints={seriesPoints}
                    highlightPoints={points}
                    selectedTag={filters.tag}
                  />
                }
                table={<ResultTable tab="both" points={points} />}
                tagSelector={
                  <TagDropdown
                    value={filters.tag}
                    tags={availableTags}
                    onChange={(tag) => setFilter({ tag })}
                  />
                }
              />
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}

function TabPanel({
  graph,
  table,
  tagSelector,
}: {
  graph: ReactNode;
  table: ReactNode;
  tagSelector: ReactNode;
}) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Tag trend graph</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Blue line is the full tag history; highlighted markers match this tab. Zoom, pan, scroll, and use the range slider to explore.
          </p>
          <div className="max-w-sm">{tagSelector}</div>
          {graph}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Detailed records</CardTitle>
        </CardHeader>
        <CardContent>{table}</CardContent>
      </Card>
    </div>
  );
}
