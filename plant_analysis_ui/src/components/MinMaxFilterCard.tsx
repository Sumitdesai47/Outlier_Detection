import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";

export function MinMaxFilterCard({ tags }: { tags: string[] }) {
  const filters = usePlantAnalysisStore((state) => state.draft.minMaxFilters);
  const addMinMaxFilter = usePlantAnalysisStore((state) => state.addMinMaxFilter);
  const updateMinMaxFilter = usePlantAnalysisStore((state) => state.updateMinMaxFilter);
  const removeMinMaxFilter = usePlantAnalysisStore((state) => state.removeMinMaxFilter);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Min–Max Filter</CardTitle>
        <CardDescription>
          Use this to remove values outside expected operating limits.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {filters.length === 0 ? (
          <p className="text-sm text-muted-foreground">No min–max rules added yet.</p>
        ) : null}
        {filters.map((filter) => (
          <div key={filter.id} className="grid gap-3 rounded-lg border p-4 md:grid-cols-4">
            <div className="space-y-2">
              <Label>Tag</Label>
              <Select
                value={filter.tag}
                onChange={(event) =>
                  updateMinMaxFilter(filter.id, { tag: event.target.value })
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
              <Label>Min value</Label>
              <Input
                type="number"
                value={filter.min}
                onChange={(event) =>
                  updateMinMaxFilter(filter.id, { min: event.target.value })
                }
              />
            </div>
            <div className="space-y-2">
              <Label>Max value</Label>
              <Input
                type="number"
                value={filter.max}
                onChange={(event) =>
                  updateMinMaxFilter(filter.id, { max: event.target.value })
                }
              />
            </div>
            <div className="flex items-end">
              <Button
                variant="ghost"
                className="text-destructive"
                onClick={() => removeMinMaxFilter(filter.id)}
              >
                <Trash2 className="h-4 w-4" />
                Remove
              </Button>
            </div>
          </div>
        ))}
        <Button variant="outline" onClick={addMinMaxFilter}>
          <Plus className="h-4 w-4" />
          Add min–max rule
        </Button>
      </CardContent>
    </Card>
  );
}
