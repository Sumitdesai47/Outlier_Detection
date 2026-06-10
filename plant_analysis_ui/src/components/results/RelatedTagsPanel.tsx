import { Link2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ModelTagEntry } from "@/types/results";

function formatMetric(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return Number(value).toFixed(3);
}

export function RelatedTagsPanel({
  primaryTag,
  modelTags,
}: {
  primaryTag: string;
  modelTags: ModelTagEntry[];
}) {
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <div className="border-b bg-muted/20 px-4 py-3">
        <div className="flex items-center gap-2">
          <Link2 className="h-4 w-4 shrink-0 text-primary" aria-hidden />
          <div>
            <h3 className="text-sm font-semibold">Related tags</h3>
            <p className="text-xs text-muted-foreground">
              Tags used to build the data model for{" "}
              <span className="font-medium text-foreground">{primaryTag}</span>
            </p>
          </div>
        </div>
      </div>

      {modelTags.length ? (
        <ul className="divide-y">
          {modelTags.map((entry, index) => (
            <li
              key={`${entry.tag}-${index}`}
              className={cn(
                "flex items-center justify-between gap-4 px-4 py-2.5",
                index % 2 === 1 && "bg-muted/10",
              )}
            >
              <span className="min-w-0 flex-1 break-words text-sm font-medium text-foreground">
                {entry.tag}
              </span>
              <span className="shrink-0 tabular-nums text-sm font-medium text-muted-foreground">
                {formatMetric(entry.corr)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="px-4 py-3 text-sm text-muted-foreground">
          No related tags are available for this tag.
        </p>
      )}
    </div>
  );
}
