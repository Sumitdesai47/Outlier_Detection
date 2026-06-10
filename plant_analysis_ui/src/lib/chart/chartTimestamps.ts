/** Parse plant timestamps for ECharts time axis without shifting calendar dates to UTC. */
export function parseChartTimestamp(value: string | null | undefined): string | null {
  if (value == null) return null;
  const text = String(value).trim();
  if (!text) return null;

  // Already ISO — keep as-is (no UTC conversion).
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) {
    return text.replace(" ", "T");
  }

  const mdy = text.match(
    /^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/,
  );
  if (mdy) {
    const [, mm, dd, yyyy, hh = "0", min = "0", sec = "0"] = mdy;
    const pad = (n: string) => n.padStart(2, "0");
    return `${yyyy}-${pad(mm)}-${pad(dd)}T${pad(hh)}:${pad(min)}:${pad(sec ?? "0")}`;
  }

  const dmy = text.match(
    /^(\d{1,2})-(\d{1,2})-(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/,
  );
  if (dmy) {
    const [, dd, mm, yyyy, hh = "0", min = "0", sec = "0"] = dmy;
    const pad = (n: string) => n.padStart(2, "0");
    return `${yyyy}-${pad(mm)}-${pad(dd)}T${pad(hh)}:${pad(min)}:${pad(sec ?? "0")}`;
  }

  const d = new Date(text);
  if (!Number.isNaN(d.getTime())) {
    return text;
  }
  return text;
}

export function chartTimestampKey(value: string | null | undefined): string {
  return parseChartTimestamp(value) ?? String(value ?? "").trim();
}
