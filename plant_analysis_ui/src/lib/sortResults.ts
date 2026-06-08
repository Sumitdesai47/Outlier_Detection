export function sortByTimestampAsc<T extends { observed_at: string | null }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => {
    const ta = a.observed_at ? new Date(a.observed_at).getTime() : Number.NaN;
    const tb = b.observed_at ? new Date(b.observed_at).getTime() : Number.NaN;
    const aValid = Number.isFinite(ta);
    const bValid = Number.isFinite(tb);
    if (!aValid && !bValid) return 0;
    if (!aValid) return 1;
    if (!bValid) return -1;
    return ta - tb;
  });
}
