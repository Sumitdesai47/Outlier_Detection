import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";
import type { Direction } from "@/types";

const options: { value: Direction; label: string; hint: string }[] = [
  { value: "both", label: "Both", hint: "Detect upward and downward changes" },
  { value: "up", label: "Up", hint: "Focus on increases above normal" },
  { value: "down", label: "Down", hint: "Focus on decreases below normal" },
];

export function DirectionSelector() {
  const direction = usePlantAnalysisStore((state) => state.draft.direction);
  const setDirection = usePlantAnalysisStore((state) => state.setDirection);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Direction Selector</CardTitle>
        <CardDescription>
          Choose whether to detect upward changes, downward changes, or both.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 md:grid-cols-3">
        {options.map((option) => (
          <label
            key={option.value}
            className={`cursor-pointer rounded-lg border p-4 transition-colors ${
              direction === option.value
                ? "border-primary bg-primary/5"
                : "hover:bg-muted/40"
            }`}
          >
            <div className="flex items-center gap-2">
              <input
                type="radio"
                name="direction"
                checked={direction === option.value}
                onChange={() => setDirection(option.value)}
              />
              <span className="font-medium">{option.label}</span>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">{option.hint}</p>
          </label>
        ))}
      </CardContent>
    </Card>
  );
}
