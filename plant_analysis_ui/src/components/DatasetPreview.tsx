import type { ParsedDataset } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function DatasetPreview({ dataset }: { dataset: ParsedDataset }) {
  const columns = dataset.columns;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Dataset Preview</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <InfoTile label="File name" value={dataset.fileName} />
          <InfoTile label="Rows" value={dataset.rowCount.toLocaleString()} />
          <InfoTile label="Columns" value={String(dataset.columnCount)} />
        </div>
        <div className="overflow-x-auto rounded-lg border">
          <table className="min-w-full text-left text-xs">
            <thead className="bg-muted/60">
              <tr>
                {columns.map((column) => (
                  <th key={column} className="px-3 py-2 font-semibold">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dataset.previewRows.map((row, index) => (
                <tr key={index} className="border-t">
                  {columns.map((column) => (
                    <td key={column} className="px-3 py-2 text-muted-foreground">
                      {String(row[column] ?? "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function InfoTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-muted/20 px-4 py-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-sm font-medium break-all">{value}</p>
    </div>
  );
}
