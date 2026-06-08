import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";
import type { DurationOption } from "@/types";

const options: { value: DurationOption; label: string }[] = [
  { value: "3m", label: "Last 3 months" },
  { value: "6m", label: "Last 6 months" },
  { value: "1y", label: "Last 1 year" },
  { value: "custom", label: "Custom range" },
];

export function DurationSelector() {
  const duration = usePlantAnalysisStore((state) => state.draft.duration);
  const customStartDate = usePlantAnalysisStore((state) => state.draft.customStartDate);
  const customEndDate = usePlantAnalysisStore((state) => state.draft.customEndDate);
  const setDuration = usePlantAnalysisStore((state) => state.setDuration);
  const setCustomDateRange = usePlantAnalysisStore((state) => state.setCustomDateRange);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Duration</CardTitle>
        <CardDescription>Select the time period to be used for analysis.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          {options.map((option) => (
            <label
              key={option.value}
              className={`cursor-pointer rounded-lg border p-4 transition-colors ${
                duration === option.value
                  ? "border-primary bg-primary/5"
                  : "hover:bg-muted/40"
              }`}
            >
              <div className="flex items-center gap-2">
                <input
                  type="radio"
                  name="duration"
                  checked={duration === option.value}
                  onChange={() => setDuration(option.value)}
                />
                <span className="text-sm font-medium">{option.label}</span>
              </div>
            </label>
          ))}
        </div>
        {duration === "custom" ? (
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="start-date">Start date</Label>
              <Input
                id="start-date"
                type="date"
                value={customStartDate}
                onChange={(event) =>
                  setCustomDateRange(event.target.value, customEndDate)
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="end-date">End date</Label>
              <Input
                id="end-date"
                type="date"
                value={customEndDate}
                onChange={(event) =>
                  setCustomDateRange(customStartDate, event.target.value)
                }
              />
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
