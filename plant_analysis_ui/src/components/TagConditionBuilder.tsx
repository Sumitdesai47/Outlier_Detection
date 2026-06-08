import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";

export function TagConditionBuilder({ tags }: { tags: string[] }) {
  const conditions = usePlantAnalysisStore((state) => state.draft.tagConditions);
  const addTagCondition = usePlantAnalysisStore((state) => state.addTagCondition);
  const updateTagCondition = usePlantAnalysisStore((state) => state.updateTagCondition);
  const removeTagCondition = usePlantAnalysisStore((state) => state.removeTagCondition);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Tag Filter Conditions</CardTitle>
        <CardDescription>
          Use this to include only data points that match specific process conditions.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {conditions.map((condition) => (
          <div key={condition.id} className="grid gap-3 rounded-lg border p-4 md:grid-cols-5">
            <div className="space-y-2">
              <Label>Tag</Label>
              <Select
                value={condition.tag}
                onChange={(event) =>
                  updateTagCondition(condition.id, { tag: event.target.value })
                }
              >
                <option value="">Select tag</option>
                {tags.map((tag) => (
                  <option key={tag} value={tag}>
                    {tag}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Condition</Label>
              <Select
                value={condition.operator}
                onChange={(event) =>
                  updateTagCondition(condition.id, {
                    operator: event.target.value as typeof condition.operator,
                  })
                }
              >
                <option value=">">&gt;</option>
                <option value="<">&lt;</option>
                <option value="=">=</option>
                <option value="between">between</option>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>{condition.operator === "between" ? "From" : "Value"}</Label>
              <Input
                value={condition.value}
                onChange={(event) =>
                  updateTagCondition(condition.id, { value: event.target.value })
                }
                placeholder={condition.operator === "between" ? "20" : "10"}
              />
            </div>
            {condition.operator === "between" ? (
              <div className="space-y-2">
                <Label>To</Label>
                <Input
                  value={condition.valueTo ?? ""}
                  onChange={(event) =>
                    updateTagCondition(condition.id, { valueTo: event.target.value })
                  }
                  placeholder="80"
                />
              </div>
            ) : (
              <div />
            )}
            <div className="flex items-end">
              <Button
                variant="ghost"
                className="text-destructive"
                onClick={() => removeTagCondition(condition.id)}
              >
                <Trash2 className="h-4 w-4" />
                Remove
              </Button>
            </div>
          </div>
        ))}
        <Button variant="outline" onClick={addTagCondition}>
          <Plus className="h-4 w-4" />
          Add condition
        </Button>
        <p className="text-xs text-muted-foreground">
          Examples: DOL &gt; 10, MFI between 20 and 80
        </p>
      </CardContent>
    </Card>
  );
}
