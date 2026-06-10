import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileSpreadsheet, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { buildQuickAnalysisFormData, runAnalysis } from "@/lib/resultsApi";
import { useResultDashboardStore } from "@/store/resultDashboardStore";

export function AreaQuickUpload({
  plantName,
  area,
  onComplete,
}: {
  plantName: string;
  area: string;
  onComplete?: () => void;
}) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const loadFilters = useResultDashboardStore((s) => s.loadFilters);

  const handleFile = async (file: File) => {
    const lower = file.name.toLowerCase();
    if (!lower.endsWith(".xlsx") && !lower.endsWith(".xls") && !lower.endsWith(".csv")) {
      toast.error("Upload .xlsx, .xls, or .csv only.");
      return;
    }
    setUploading(true);
    try {
      const formData = buildQuickAnalysisFormData({ plantName, area, file });
      const result = await runAnalysis(formData);
      await loadFilters();
      toast.success(`Analysis completed for ${plantName} · ${area}.`);
      onComplete?.();
      navigate(
        `/results?plant=${encodeURIComponent(plantName)}&area=${encodeURIComponent(area)}&run_id=${result.run_id}`,
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Upload failed.");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls,.csv"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void handleFile(file);
        }}
      />
      <Button
        variant="outline"
        className="h-8 gap-1.5 text-xs"
        disabled={uploading}
        onClick={() => inputRef.current?.click()}
      >
        {uploading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <FileSpreadsheet className="h-3.5 w-3.5" />
        )}
        {uploading ? "Analyzing…" : "Upload Excel"}
      </Button>
    </>
  );
}
