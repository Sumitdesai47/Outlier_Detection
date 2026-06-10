import { Calendar, LineChart, Tags } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { LiveDashboardOverview } from "@/types/liveResults";

export function LiveSummaryCards({ overview }: { overview: LiveDashboardOverview }) {
  const summary = overview.summary || {};
  const totalTags = Number(summary.total_tags_analyzed ?? summary.Total_Tags ?? 0);
  const strongToday = overview.drifts.length;

  const cards = [
    {
      label: "Observation days",
      value: overview.observation_days.length,
      icon: Calendar,
    },
    {
      label: "Tags analyzed",
      value: totalTags || "—",
      icon: Tags,
    },
    {
      label: "Strong anomaly tags (selected day)",
      value: strongToday,
      icon: LineChart,
    },
  ];

  return (
    <div className="grid gap-4 md:grid-cols-3">
      {cards.map(({ label, value, icon: Icon }) => (
        <Card key={label}>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
            <Icon className="h-4 w-4 text-primary" />
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold">{value}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
