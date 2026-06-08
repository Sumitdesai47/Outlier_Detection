import { useCallback, useState } from "react";
import { FileSpreadsheet, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { parseDatasetFile } from "@/lib/parseDataset";
import type { ParsedDataset } from "@/types";

interface FileUploadProps {
  onParsed: (dataset: ParsedDataset) => void;
  onFileSelected?: (file: File) => void;
}

export function FileUpload({ onParsed, onFileSelected }: FileUploadProps) {
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleFile = useCallback(
    async (file: File) => {
      setLoading(true);
      try {
        onFileSelected?.(file);
        const dataset = await parseDatasetFile(file);
        onParsed(dataset);
        toast.success("Dataset uploaded and parsed successfully.");
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to parse file.";
        toast.error(message);
      } finally {
        setLoading(false);
      }
    },
    [onParsed, onFileSelected],
  );

  const onDrop = async (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) await handleFile(file);
  };

  return (
    <div className="space-y-3">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex min-h-[180px] flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors ${
          dragging ? "border-primary bg-primary/5" : "border-border bg-muted/30"
        }`}
      >
        <UploadCloud className="mb-3 h-10 w-10 text-primary" />
        <p className="text-sm font-medium">Drag and drop your plant data file here</p>
        <p className="mt-1 text-xs text-muted-foreground">Supported: .xlsx, .xls, .csv</p>
        <label className="mt-4">
          <input
            type="file"
            accept=".xlsx,.xls,.csv"
            className="hidden"
            onChange={async (event) => {
              const file = event.target.files?.[0];
              if (file) await handleFile(file);
            }}
          />
          <span className="inline-flex h-10 cursor-pointer items-center justify-center rounded-md bg-secondary px-4 text-sm font-medium text-secondary-foreground hover:bg-secondary/80">
            {loading ? "Parsing…" : "Browse files"}
          </span>
        </label>
      </div>
      <p className="text-xs text-muted-foreground">
        Upload historical plant data. The system will automatically identify timestamps and sensor tags.
      </p>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <FileSpreadsheet className="h-4 w-4" />
        Excel and CSV wide-format time series files are supported.
      </div>
    </div>
  );
}
