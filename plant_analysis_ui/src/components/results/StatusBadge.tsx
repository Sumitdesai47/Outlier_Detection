import { cn } from "@/lib/utils";
import type { ResultStatus } from "@/types/results";

const styles: Record<ResultStatus, string> = {
  Normal: "bg-emerald-100 text-emerald-800 border-emerald-200",
  "Outlier Only": "bg-red-100 text-red-800 border-red-200",
  "Process Issue Only": "bg-amber-100 text-amber-900 border-amber-200",
  Both: "bg-purple-100 text-purple-800 border-purple-200",
};

export function StatusBadge({ status }: { status: ResultStatus | string }) {
  const cls = styles[status as ResultStatus] ?? "bg-muted text-muted-foreground";
  return (
    <span className={cn("inline-flex rounded-full border px-2.5 py-0.5 text-xs font-medium", cls)}>
      {status}
    </span>
  );
}
