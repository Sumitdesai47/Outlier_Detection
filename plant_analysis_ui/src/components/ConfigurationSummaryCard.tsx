import { Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";
interface ConfigurationSummaryCardProps {
  canSave: boolean;
  onSave: () => void;
}

export function ConfigurationSummaryCard({ canSave, onSave }: ConfigurationSummaryCardProps) {
  const draft = usePlantAnalysisStore((state) => state.draft);
  const plants = usePlantAnalysisStore((state) => state.plants);
  const plant = plants.find((item) => item.id === draft.plantId);

  const rows = [
    { label: "Selected plant", value: plant?.name || "—" },
    { label: "Selected subsystem", value: draft.subsystem || "—" },
    { label: "Uploaded file", value: draft.dataset?.fileName || "—" },
    {
      label: "Detected timestamp column",
      value: draft.dataset?.timestampColumn || "Not detected",
    },
    {
      label: "Detected tag columns",
      value: draft.dataset ? String(draft.dataset.tagColumns.length) : "—",
    },
    { label: "Selected direction", value: draft.direction },
    {
      label: "Min–max filters",
      value: String(draft.minMaxFilters.filter((f) => f.tag).length),
    },
    {
      label: "Tag filter conditions",
      value: String(draft.tagConditions.filter((c) => c.tag && c.value).length),
    },
    {
      label: "Critical tags",
      value: draft.criticalTags.length ? draft.criticalTags.join(", ") : "None selected",
    },
  ];

  return (
    <Card className="border-primary/20 bg-primary/5">
      <CardHeader>
        <CardTitle>Configuration Summary</CardTitle>
        <CardDescription>
          Review your selections before saving. Analysis will run and open the Result Dashboard.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 md:grid-cols-2">
          {rows.map((row) => (
            <div key={row.label} className="rounded-lg border bg-card px-4 py-3">
              <p className="text-xs text-muted-foreground">{row.label}</p>
              <p className="mt-1 text-sm font-medium break-words">{row.value}</p>
            </div>
          ))}
        </div>
        <Button disabled={!canSave} onClick={onSave} className="w-full sm:w-auto">
          <Save className="h-4 w-4" />
          Save & Run Analysis
        </Button>
      </CardContent>
    </Card>
  );
}
