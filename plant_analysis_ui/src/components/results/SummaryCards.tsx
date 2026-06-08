import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/results/StatusBadge";
import { summaryCombinedAbnormal, visibleStatusDistribution } from "@/lib/summaryMetrics";
import type { ResultSummary } from "@/types/results";

export function SummaryCards({ summary }: { summary: ResultSummary }) {
  const outlierExclusive = summary.total_outlier_points;
  const processExclusive = summary.total_process_issue_points;
  const combinedAbnormal = summaryCombinedAbnormal(summary);

  const kpis = [
    { label: "Total tags analyzed", value: summary.total_tags_analyzed },
    { label: "Total records processed", value: summary.total_records_processed },
    { label: "Outlier", value: outlierExclusive },
    { label: "Process issue", value: processExclusive },
    { label: "Both", value: combinedAbnormal },
    { label: "Dataset window", value: summary.analysis_duration || "Full uploaded dataset" },
  ];

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {kpis.map((kpi) => (
          <Card key={kpi.label}>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">{kpi.label}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-semibold">{kpi.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Run context</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2 lg:grid-cols-4 text-sm">
          <Meta label="Plant" value={summary.plant_name} />
          <Meta label="Subsystem" value={summary.subsystem} />
          <Meta label="Dataset" value={summary.dataset_name} />
          <Meta label="Last processed" value={new Date(summary.last_processed_at).toLocaleString()} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Status distribution</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          {visibleStatusDistribution(summary.status_distribution).map(({ status, count }) => (
            <div key={status} className="flex items-center gap-2 rounded-lg border px-3 py-2">
              <StatusBadge status={status} />
              <span className="text-sm font-medium">{count}</span>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="font-medium break-words">{value}</p>
    </div>
  );
}
