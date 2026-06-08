import { Download } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { downloadUrl } from "@/lib/resultsApi";
import type { ResultTab } from "@/types/results";

export function DownloadButton({
  runId,
  tab,
  tag,
}: {
  runId: string;
  tab: ResultTab;
  tag?: string;
}) {
  const trigger = (format: "csv" | "xlsx" | "pdf", label: string) => {
    if (!runId) {
      toast.error("No analysis run selected.");
      return;
    }
    window.open(downloadUrl({ runId, tab, format, tag }), "_blank");
    toast.success(`${label} download started.`);
  };

  return (
    <div className="flex flex-wrap gap-2">
      <Button variant="outline" className="h-9 px-3 text-xs" onClick={() => trigger("csv", "CSV")}>
        <Download className="h-4 w-4" /> CSV
      </Button>
      <Button variant="outline" className="h-9 px-3 text-xs" onClick={() => trigger("xlsx", "Excel")}>
        <Download className="h-4 w-4" /> Excel
      </Button>
      <Button variant="outline" className="h-9 px-3 text-xs" onClick={() => trigger("pdf", "Report")}>
        <Download className="h-4 w-4" /> PDF report
      </Button>
    </div>
  );
}
