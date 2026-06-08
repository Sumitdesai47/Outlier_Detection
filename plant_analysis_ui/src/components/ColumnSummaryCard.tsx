import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { ParsedDataset } from "@/types";

export function ColumnSummaryCard({ dataset }: { dataset: ParsedDataset }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Detected Column Summary</CardTitle>
        <CardDescription>
          Automatically identified timestamp, sensor tags, and non-numeric columns.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <SummaryRow
          label="Timestamp column"
          value={dataset.timestampColumn ?? "Not detected"}
          highlight={!!dataset.timestampColumn}
        />
        <SummaryRow
          label="Tag / sensor columns"
          value={`${dataset.tagColumns.length} detected`}
        />
        <div className="flex flex-wrap gap-2">
          {dataset.tagColumns.slice(0, 12).map((tag) => (
            <Badge key={tag} className="bg-primary/10 text-primary">
              {tag}
            </Badge>
          ))}
          {dataset.tagColumns.length > 12 ? (
            <Badge className="bg-muted text-muted-foreground">
              +{dataset.tagColumns.length - 12} more
            </Badge>
          ) : null}
        </div>
        <SummaryRow
          label="Numeric columns"
          value={String(dataset.numericColumns.length)}
        />
        <SummaryRow
          label="Non-numeric columns"
          value={String(dataset.nonNumericColumns.length)}
        />
      </CardContent>
    </Card>
  );
}

function SummaryRow({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border px-4 py-3">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className={`text-sm font-medium ${highlight ? "text-primary" : ""}`}>{value}</span>
    </div>
  );
}
