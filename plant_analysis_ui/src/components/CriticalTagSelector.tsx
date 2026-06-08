import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";

export function CriticalTagSelector({ tags }: { tags: string[] }) {
  const criticalTags = usePlantAnalysisStore((state) => state.draft.criticalTags);
  const toggleCriticalTag = usePlantAnalysisStore((state) => state.toggleCriticalTag);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Critical Tags</CardTitle>
        <CardDescription>
          Critical tags are the most important sensors for monitoring this subsystem.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {tags.length === 0 ? (
          <p className="text-sm text-muted-foreground">Upload a dataset to see available tags.</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {tags.map((tag) => (
              <label
                key={tag}
                className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-muted/30"
              >
                <input
                  type="checkbox"
                  checked={criticalTags.includes(tag)}
                  onChange={() => toggleCriticalTag(tag)}
                />
                <span>{tag}</span>
              </label>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
