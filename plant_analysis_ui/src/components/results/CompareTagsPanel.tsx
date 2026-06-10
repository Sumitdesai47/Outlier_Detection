import { Button } from "@/components/ui/button";
import type { ModelTagEntry } from "@/types/results";

export function CompareTagsPanel({
  primaryTag,
  allTags,
  modelTags,
  compareTags,
  onSelectCompareTags,
  onClear,
}: {
  primaryTag: string;
  allTags: string[];
  modelTags: ModelTagEntry[];
  compareTags: string[];
  onSelectCompareTags: (tags: string[]) => void;
  onClear: () => void;
}) {
  const modelTagNames = new Set(modelTags.map((m) => m.tag));
  const compareOptions = [...new Set(allTags)].filter((t) => t && t !== primaryTag).sort();

  return (
    <div className="space-y-4 rounded-lg border bg-muted/20 p-4">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium">Compare other tags on the chart</p>
          {compareTags.length ? (
            <Button type="button" variant="ghost" className="h-7 text-xs" onClick={onClear}>
              Clear selection
            </Button>
          ) : null}
        </div>
        <p className="text-xs text-muted-foreground">
          Select one or more tags to overlay on the chart with the primary tag (hold Ctrl/Cmd for
          multi-select in the list).
        </p>
        {compareOptions.length ? (
          <select
            multiple
            size={Math.min(10, Math.max(4, compareOptions.length))}
            className="w-full max-w-xl rounded-lg border bg-background px-2 py-2 font-mono text-xs"
            value={compareTags}
            onChange={(e) => {
              const selected = Array.from(e.target.selectedOptions).map((o) => o.value);
              onSelectCompareTags(selected);
            }}
          >
            {compareOptions.map((tag) => (
              <option key={tag} value={tag}>
                {modelTagNames.has(tag) ? `• ${tag} (model tag)` : tag}
              </option>
            ))}
          </select>
        ) : (
          <p className="text-xs text-amber-800">
            No other tags available to compare. Dataset tag list was not stored — re-run analysis.
          </p>
        )}
        {compareTags.length ? (
          <p className="text-xs text-muted-foreground">
            Comparing:{" "}
            <span className="font-mono">{compareTags.join(", ")}</span>
          </p>
        ) : null}
      </div>
    </div>
  );
}
