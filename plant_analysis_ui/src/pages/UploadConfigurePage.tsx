import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { TopBar } from "@/components/layout/TopBar";
import { PlantDropdown } from "@/components/PlantDropdown";
import { SubsystemDropdown } from "@/components/SubsystemDropdown";
import { FileUpload } from "@/components/FileUpload";
import { DatasetPreview } from "@/components/DatasetPreview";
import { ColumnSummaryCard } from "@/components/ColumnSummaryCard";
import { FilterBuilder } from "@/components/FilterBuilder";
import { DirectionSelector } from "@/components/DirectionSelector";
import { CriticalTagSelector } from "@/components/CriticalTagSelector";
import { ConfigurationSummaryCard } from "@/components/ConfigurationSummaryCard";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { runAnalysis } from "@/lib/resultsApi";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";

export function UploadConfigurePage() {
  const navigate = useNavigate();
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);

  const draft = usePlantAnalysisStore((state) => state.draft);
  const plants = usePlantAnalysisStore((state) => state.plants);
  const setDraftPlant = usePlantAnalysisStore((state) => state.setDraftPlant);
  const setDraftSubsystem = usePlantAnalysisStore((state) => state.setDraftSubsystem);
  const setDraftDataset = usePlantAnalysisStore((state) => state.setDraftDataset);
  const saveConfiguration = usePlantAnalysisStore((state) => state.saveConfiguration);

  const tags = draft.dataset?.tagColumns ?? [];
  const plant = plants.find((item) => item.id === draft.plantId);

  const durationValid =
    draft.duration !== "custom" ||
    (draft.customStartDate.trim() !== "" && draft.customEndDate.trim() !== "");

  const canSave = Boolean(
    draft.plantId && draft.subsystem && draft.dataset && uploadedFile && durationValid,
  );

  const handleSave = async () => {
    if (!plant || !uploadedFile || !draft.dataset) return;
    setSaving(true);
    try {
      saveConfiguration();

      const formData = new FormData();
      formData.append("plant_name", plant.name);
      formData.append("subsystem", draft.subsystem);
      formData.append("dataset_name", draft.dataset.fileName);
      formData.append("file", uploadedFile);
      formData.append(
        "config_json",
        JSON.stringify({
          direction: draft.direction,
          minMaxFilters: draft.minMaxFilters,
          tagConditions: draft.tagConditions,
          critical_tags: draft.criticalTags,
        }),
      );

      const result = await runAnalysis(formData);
      toast.success("Multimodel outlier analysis completed. Opening results dashboard.");
      navigate(
        `/results?plant=${encodeURIComponent(plant.name)}&area=${encodeURIComponent(draft.subsystem)}&run_id=${result.run_id}`,
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to run analysis.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <TopBar
        title="Upload & Configure Analysis"
        subtitle="Upload plant data, configure filters, and run the same multimodel outlier engine used in Multimodel Outlier Detection."
      />
      <div className="space-y-6 p-6">
        <Card>
          <CardHeader>
            <CardTitle>Step 1: Select Plant & Subsystem</CardTitle>
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
          </CardHeader>
          <CardContent className="space-y-6">
            <FileUpload onParsed={setDraftDataset} onFileSelected={setUploadedFile} />
            {draft.dataset ? (
              <>
                <DatasetPreview dataset={draft.dataset} />
                <ColumnSummaryCard dataset={draft.dataset} />
              </>
            ) : null}
          </CardContent>
        </Card>

        <div className="space-y-2">
          <h3 className="text-lg font-semibold">Step 3: Configure Analysis Options</h3>
          <p className="text-sm text-muted-foreground">
            Set operating limits, process conditions, critical sensors, and analysis duration.
          </p>
        </div>

        <FilterBuilder tags={tags} />
        <DirectionSelector />
        <CriticalTagSelector tags={tags} />

        <ConfigurationSummaryCard
          canSave={canSave && !saving}
          onSave={handleSave}
        />
      </div>
    </div>
  );
}
