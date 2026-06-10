import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { TopBar } from "@/components/layout/TopBar";
import { LiveConfigurationSummaryCard } from "@/components/LiveConfigurationSummaryCard";
import { PlantDropdown } from "@/components/PlantDropdown";
import { SubsystemDropdown } from "@/components/SubsystemDropdown";
import { FileUpload } from "@/components/FileUpload";
import { DatasetPreview } from "@/components/DatasetPreview";
import { ColumnSummaryCard } from "@/components/ColumnSummaryCard";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { liveOutlierResultsUrl, runLiveOutlierUpload } from "@/lib/liveOutlierUpload";
import { useLiveResultDashboardStore } from "@/store/liveResultDashboardStore";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";

export function LiveUploadConfigurePage() {
  const navigate = useNavigate();
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [datasetName, setDatasetName] = useState("");
  const [saving, setSaving] = useState(false);

  const draft = usePlantAnalysisStore((state) => state.draft);
  const plants = usePlantAnalysisStore((state) => state.plants);
  const setDraftPlant = usePlantAnalysisStore((state) => state.setDraftPlant);
  const setDraftSubsystem = usePlantAnalysisStore((state) => state.setDraftSubsystem);
  const setDraftDataset = usePlantAnalysisStore((state) => state.setDraftDataset);
  const loadLiveFilters = useLiveResultDashboardStore((s) => s.loadFilters);

  const plant = plants.find((item) => item.id === draft.plantId);

  const canSave = Boolean(
    draft.plantId &&
      draft.subsystem &&
      draft.dataset &&
      uploadedFile &&
      (datasetName.trim() || draft.dataset.fileName),
  );

  const handleSave = async () => {
    if (!plant || !uploadedFile || !draft.dataset) return;
    const resolvedDatasetName = datasetName.trim() || draft.dataset.fileName;
    setSaving(true);
    try {
      const result = await runLiveOutlierUpload({
        plantName: plant.name,
        area: draft.subsystem,
        file: uploadedFile,
        datasetName: resolvedDatasetName,
        timestampColumn: draft.dataset.timestampColumn ?? undefined,
      });
      await loadLiveFilters();
      toast.success("Live Outlier analysis completed. Opening Live Result Dashboard.");
      navigate(
        liveOutlierResultsUrl({
          plantName: plant.name,
          area: draft.subsystem,
          runId: result.run_id,
        }),
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to run live analysis.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <TopBar
        title="Live Upload & Configure Analysis"
        subtitle="Same V5 pipeline as Live outlier data upload — results saved to the database and shown on Live Result Dashboard."
      />
      <div className="space-y-6 p-6">
        <Card className="border-primary/20 bg-primary/5">
          <CardContent className="p-5 text-sm text-muted-foreground">
            <p>
              This tab uses the <strong>same methodology</strong> as the main app{" "}
              <strong>Live outlier data upload</strong> tab: wide Excel with a{" "}
              <code className="text-xs">Timestamp</code> column, V5 outlier detection, database
              persistence, and UTC day view on the{" "}
              <Link
                to="/live-results"
                className="font-medium text-primary underline-offset-2 hover:underline"
              >
                Live Result Dashboard
              </Link>
              .
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Step 1: Select Plant &amp; Subsystem</CardTitle>
            <CardDescription>
              Select the plant and subsystem where this dataset belongs.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <PlantDropdown value={draft.plantId} onChange={setDraftPlant} />
            <SubsystemDropdown
              plantId={draft.plantId}
              value={draft.subsystem}
              onChange={setDraftSubsystem}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Step 2: Upload Data</CardTitle>
            <CardDescription>
              Wide-format Excel (first sheet, <code className="text-xs">Timestamp</code> + numeric
              tags) — same layout as Live outlier data upload. CSV is also accepted.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="max-w-md space-y-2">
              <Label htmlFor="live-dataset-name">Dataset name</Label>
              <Input
                id="live-dataset-name"
                placeholder="e.g. Plant A — weekly export"
                value={datasetName}
                onChange={(e) => setDatasetName(e.target.value)}
                maxLength={512}
              />
              <p className="text-xs text-muted-foreground">
                Stored with plant and area in the analysis database (same role as Live outlier data
                upload).
              </p>
            </div>
            <FileUpload
              onParsed={(dataset) => {
                setDraftDataset(dataset);
                if (!datasetName.trim()) setDatasetName(dataset.fileName);
              }}
              onFileSelected={setUploadedFile}
            />
            {draft.dataset ? (
              <>
                <DatasetPreview dataset={draft.dataset} />
                <ColumnSummaryCard dataset={draft.dataset} />
              </>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Step 3: Live Outlier analysis</CardTitle>
            <CardDescription>
              Runs V5 (Testing deviation spike) on save — same engine as Live outlier data upload.
              Strong-anomaly tags, plots, and compare-tag correlations are precomputed for the
              dashboard.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            <ul className="list-inside list-disc space-y-1">
              <li>UTC calendar day picker on the results page</li>
              <li>Strong anomaly tags ranked per day</li>
              <li>Trend plot with correlated compare tags</li>
              <li>No multimodel S1–S8 consensus — V5 only</li>
            </ul>
          </CardContent>
        </Card>

        <LiveConfigurationSummaryCard
          canSave={canSave}
          saving={saving}
          datasetName={datasetName.trim() || draft.dataset?.fileName}
          onSave={() => void handleSave()}
        />
      </div>
    </div>
  );
}
