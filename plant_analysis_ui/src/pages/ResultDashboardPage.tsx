import { useEffect, type ReactNode } from "react";

import { useNavigate, useSearchParams } from "react-router-dom";

import { Calendar } from "lucide-react";

import { TopBar } from "@/components/layout/TopBar";

import { CompareTagsPanel } from "@/components/results/CompareTagsPanel";
import { RelatedTagsPanel } from "@/components/results/RelatedTagsPanel";

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
  datasetTagsFromSummary,
  mergeModelTagEntries,
  modelTagsForTag,
  relatedTagsFromPoints,
} from "@/lib/tagContext";
import { tagOutlierExclusive, tagProcessExclusive } from "@/lib/summaryMetrics";

import { useResultDashboardStore } from "@/store/resultDashboardStore";



export function ResultDashboardPage() {

  const navigate = useNavigate();

  const [searchParams] = useSearchParams();

  const {

    activeTab,

    filters,

    runs,

    availableTags,

    compareTags,

    tagContext,

    dayMeta,

    summary,

    points,

    seriesPoints,

    loading,

    error,

    setActiveTab,

    setSelectedDay,

    setFilter,

    setCompareTags,

    clearCompareTags,

    loadFilters,

  } = useResultDashboardStore();



  useEffect(() => {

    const plant = searchParams.get("plant") ?? "";

    const area = searchParams.get("area") ?? "";

    const runId = searchParams.get("run_id") ?? "";



    void loadFilters().then(() => {

      if (runId) setFilter({ runId });

      else if (plant && area) setFilter({ plant, subsystem: area });

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



  const graphCommon = {

    seriesPoints,

    highlightPoints: points,

    selectedTag: filters.tag,

    compareTags,

  };



  const allCompareTags = (() => {

    const fromSummary = datasetTagsFromSummary(summary);

    if (fromSummary.length) return fromSummary;

    if (tagContext?.all_tags?.length) return tagContext.all_tags;

    return availableTags;

  })();



  const modelTags = mergeModelTagEntries(
    modelTagsForTag(summary, filters.tag),
    tagContext?.model_tags,
    relatedTagsFromPoints(points, filters.tag),
    relatedTagsFromPoints(seriesPoints, filters.tag),
  );



  const relatedTagsPanel = filters.tag ? (

    <RelatedTagsPanel primaryTag={filters.tag} modelTags={modelTags} />

  ) : null;



  const comparePanel = filters.tag ? (

    <CompareTagsPanel

      primaryTag={filters.tag}

      allTags={allCompareTags}

      modelTags={modelTags}

      compareTags={compareTags}

      onSelectCompareTags={setCompareTags}

      onClear={clearCompareTags}

    />

  ) : null;



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



            {dayMeta?.observation_days?.length ? (

              <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-muted/30 px-4 py-3">

                <div className="flex items-center gap-2 text-sm text-muted-foreground">

                  <Calendar className="h-4 w-4" />

                  <label htmlFor="results-day">Filter by day</label>

                </div>

                <select

                  id="results-day"

                  className="rounded-lg border bg-background px-3 py-2 text-sm"

                  value={filters.selectedDay || ""}

                  onChange={(event) => setSelectedDay(event.target.value)}

                >

                  <option value="">All days</option>

                  {dayMeta.observation_days.map((day) => (

                    <option key={day} value={day}>

                      {day}

                    </option>

                  ))}

                </select>

                <span className="text-xs text-muted-foreground">

                  {dayMeta.cooling_period_rows

                    ? `First ${dayMeta.cooling_period_rows} rows = cooling period. `

                    : ""}

                  {dayMeta.analyzed_timestamps ?? dayMeta.observation_days.length} analyzed day

                  {(dayMeta.analyzed_timestamps ?? dayMeta.observation_days.length) === 1

                    ? ""

                    : "s"}

                </span>

              </div>

            ) : null}



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

                            <th className="px-3 py-2">Tag issue</th>

                            <th className="px-3 py-2">Process</th>

                          </tr>

                        </thead>

                        <tbody>

                          {summary.tag_summaries.map((row) => (

                            <tr key={row.tag_name} className="border-t">

                              <td className="px-3 py-2">{row.tag_name}</td>

                              <td className="px-3 py-2">{row.total_points}</td>

                              <td className="px-3 py-2">{tagOutlierExclusive(row)}</td>

                              <td className="px-3 py-2">{tagProcessExclusive(row)}</td>

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

                graph={<OutlierGraph {...graphCommon} />}

                table={<ResultTable tab="outlier" points={points} />}

                tagSelector={

                  <TagDropdown

                    value={filters.tag}

                    tags={availableTags}

                    onChange={(tag) => setFilter({ tag })}

                  />

                }

                relatedTagsPanel={relatedTagsPanel}

                comparePanel={comparePanel}

              />

            ) : null}



            {!loading && activeTab === "process" ? (

              <TabPanel

                graph={<ProcessIssueGraph {...graphCommon} />}

                table={<ResultTable tab="process" points={points} />}

                tagSelector={

                  <TagDropdown

                    value={filters.tag}

                    tags={availableTags}

                    onChange={(tag) => setFilter({ tag })}

                  />

                }

                relatedTagsPanel={relatedTagsPanel}

                comparePanel={comparePanel}

              />

            ) : null}



            {!loading && activeTab === "both" ? (

              <TabPanel

                graph={<BothIssueGraph {...graphCommon} />}

                table={<ResultTable tab="both" points={points} />}

                tagSelector={

                  <TagDropdown

                    value={filters.tag}

                    tags={availableTags}

                    onChange={(tag) => setFilter({ tag })}

                  />

                }

                relatedTagsPanel={relatedTagsPanel}

                comparePanel={comparePanel}

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

  relatedTagsPanel,

  comparePanel,

}: {

  graph: ReactNode;

  table: ReactNode;

  tagSelector: ReactNode;

  relatedTagsPanel?: ReactNode;

  comparePanel?: ReactNode;

}) {

  return (

    <div className="space-y-4">

      <Card>

        <CardHeader>

          <CardTitle>Tag trend graph</CardTitle>

        </CardHeader>

        <CardContent className="space-y-4">

          <div className="max-w-sm">{tagSelector}</div>

          {graph}

          {relatedTagsPanel}

          {comparePanel}

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

