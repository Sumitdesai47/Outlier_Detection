const TIMESTAMP_HINTS = [
  "timestamp",
  "time",
  "date",
  "datetime",
  "ts",
  "recorded",
  "sample_time",
];

function isNumericValue(value: unknown): boolean {
  if (value === null || value === undefined || value === "") return false;
  const n = Number(value);
  return !Number.isNaN(n) && Number.isFinite(n);
}

function looksLikeTimestampColumn(name: string, values: unknown[]): boolean {
  const lower = name.toLowerCase();
  if (TIMESTAMP_HINTS.some((hint) => lower.includes(hint))) return true;
  const sample = values.filter((v) => v !== null && v !== undefined && v !== "").slice(0, 20);
  if (sample.length === 0) return false;
  const parsed = sample.filter((v) => !Number.isNaN(Date.parse(String(v))));
  return parsed.length / sample.length >= 0.7;
}

export function detectColumns(rows: Record<string, unknown>[], columns: string[]) {
  const numericColumns: string[] = [];
  const nonNumericColumns: string[] = [];
  let timestampColumn: string | null = null;

  for (const col of columns) {
    const values = rows.map((row) => row[col]).slice(0, 200);
    const nonEmpty = values.filter((v) => v !== null && v !== undefined && v !== "");
    const numericRatio =
      nonEmpty.length === 0
        ? 0
        : nonEmpty.filter((v) => isNumericValue(v)).length / nonEmpty.length;

    if (!timestampColumn && looksLikeTimestampColumn(col, values)) {
      timestampColumn = col;
      nonNumericColumns.push(col);
      continue;
    }

    if (numericRatio >= 0.85) {
      numericColumns.push(col);
    } else {
      nonNumericColumns.push(col);
    }
  }

  const tagColumns = numericColumns.filter((col) => col !== timestampColumn);

  if (!timestampColumn) {
    const fallback = columns.find((col) => looksLikeTimestampColumn(col, rows.map((r) => r[col])));
    timestampColumn = fallback ?? null;
  }

  return {
    timestampColumn,
    tagColumns,
    numericColumns,
    nonNumericColumns,
  };
}
