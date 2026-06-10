import { plotMarkerLabel, resolvePlotMarkerKey } from "@/lib/chart/chartMarkers";
import type { ResultPoint } from "@/types/results";

export function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatTimestampShort(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatNumber(value: number | null | undefined, digits = 4): string {
  if (value == null || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  });
}

export function formatBand(lower: number | null, upper: number | null): string {
  const lo = formatNumber(lower);
  const hi = formatNumber(upper);
  if (lo === "—" && hi === "—") return "—";
  if (lo === "—") return `≤ ${hi}`;
  if (hi === "—") return `≥ ${lo}`;
  return `${lo} – ${hi}`;
}

export function issueTypeLabel(status: string): string {
  if (status === "Outlier Only") return "Tag issue";
  if (status === "Process Issue Only") return "Process issue";
  return status;
}

export function isTagIssue(point: ResultPoint): boolean {
  return point.status === "Outlier Only";
}

export function markerTypeLabel(point: ResultPoint): string {
  const key = resolvePlotMarkerKey(point);
  if (key) return plotMarkerLabel(key);
  return point.final_class || "Unknown";
}

export function isSuddenJump(point: ResultPoint): boolean {
  return markerTypeLabel(point) === "Sudden jump";
}

export function s5PeerLabel(point: ResultPoint): string {
  if (point.s5_peer_fired === true) return "S5 failed";
  if (point.s5_peer_fired === false) return "S5 passed";
  return "S5 unknown";
}

export function parseReasonBullets(reason: string | null): string[] {
  if (!reason?.trim()) return ["No explanation stored for this point."];
  return reason
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export function tableSubtitle(tab: string): string {
  if (tab === "outlier") {
    return "Outlier detected with S5 peer engine failed — tag does not match peers.";
  }
  if (tab === "process") {
    return "Outlier detected with S5 peer engine passed — wider process shift pattern.";
  }
  return "All abnormal outliers — tag issue and process issue.";
}
