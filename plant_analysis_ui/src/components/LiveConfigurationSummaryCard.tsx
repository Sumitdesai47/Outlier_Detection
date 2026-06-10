import { Activity } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";

interface LiveConfigurationSummaryCardProps {
  canSave: boolean;
  saving: boolean;
  datasetName?: string;
  onSave: () => void;
}

export function LiveConfigurationSummaryCard({
  canSave,
  saving,
  datasetName,
  onSave,
}: LiveConfigurationSummaryCardProps) {
  const draft = usePlantAnalysisStore((state) => state.draft);
  const plants = usePlantAnalysisStore((state) => state.plants);
  const plant = plants.find((item) => item.id === draft.plantId);

  const rows = [
    { label: "Selected plant", value: plant?.name || "—" },
    { label: "Selected subsystem", value: draft.subsystem || "—" },
    { label: "Dataset name", value: datasetName || draft.dataset?.fileName || "—" },
    { label: "Uploaded file", value: draft.dataset?.fileName || "—" },
    {
      label: "Detected timestamp column",
      value: draft.dataset?.timestampColumn || "Not detected",
    },
    {
      label: "Detected tag columns",
      value: draft.dataset ? String(draft.dataset.tagColumns.length) : "—",
    },
    { label: "Analysis engine", value: "Live Outlier (V5)" },
  ];

  return (
    <Card className="border-primary/20 bg-primary/5">
      <CardHeader>
        <CardTitle>Configuration Summary</CardTitle>
        <CardDescription>
          Review before saving. Uses the Live outlier data upload V5 pipeline; results are stored in
          the database and open on the Live Result Dashboard.
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
        <Button disabled={!canSave || saving} onClick={onSave} className="w-full gap-2 sm:w-auto">
          <Activity className="h-4 w-4" />
          {saving ? "Uploading & analyzing…" : "Save & Run Live Analysis"}
        </Button>
      </CardContent>
    </Card>
  );
}
