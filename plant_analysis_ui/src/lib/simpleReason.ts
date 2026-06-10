import type { FailedEngine, ResultPoint } from "@/types/results";

function formatValue(value: number | null | undefined): string | null {
  if (value == null || !Number.isFinite(value)) return null;
  return Number(value.toFixed(3)).toString();
}

function engineCause(engine: FailedEngine): string | null {
  const id = engine.id.toLowerCase();
  const label = engine.label.toLowerCase();
  if (id.includes("s4") || label.includes("jump") || label.includes("step")) {
    return "the reading changed suddenly compared with the previous value";
  }
  if (
    id.includes("s3") ||
    id.includes("fence") ||
    label.includes("range") ||
    label.includes("fence")
  ) {
    return "the value was outside its normal operating band";
  }
  if (id.includes("s1") || label.includes("overall") || label.includes("history")) {
    return "the value was much higher or lower than this tag normally runs";
  }
  if (id.includes("s2") || label.includes("recent")) {
    return "the value did not match the recent pattern for this tag";
  }
  if (id.includes("s6") || label.includes("long-term") || label.includes("level shift")) {
    return "the tag shifted away from its longer-term normal level";
  }
  if (id.includes("s7") || label.includes("trend")) {
    return "the recent trend moved away from the longer-term trend";
  }
  if (id.includes("s8") || label.includes("baseline")) {
    return "the value did not match the early baseline pattern";
  }
  if (id.includes("s5") || label.includes("similar") || label.includes("peer")) {
    return "similar tags did not support this tag's behavior";
  }
  return null;
}

function joinCauses(causes: string[]): string {
  const unique = [...new Set(causes)].slice(0, 3);
  if (!unique.length) return "the reading did not match normal behavior";
  if (unique.length === 1) return unique[0];
  if (unique.length === 2) return `${unique[0]} and ${unique[1]}`;
  return `${unique[0]}, ${unique[1]}, and ${unique[2]}`;
}

export function simpleReasonText(point: ResultPoint): string {
  const engines = failedEnginesForPoint(point);
  const actual = formatValue(point.tag_value);
  const expected = formatValue(point.predicted_value);
  const valueText =
    actual && expected
      ? `Measured value was ${actual}; the model expected about ${expected}. `
      : actual
        ? `Measured value was ${actual}. `
        : "";

  if (engines.length) {
    const causeText = joinCauses(engines.map(engineCause).filter(Boolean) as string[]);
    const issueText =
      point.s5_peer_fired === true
        ? "Related tags did not show the same behavior, so this may be a sensor, transmitter, or local control issue for this tag."
        : point.s5_peer_fired === false
          ? "Related tags moved in a similar way, so this is more likely a wider process change than one bad tag."
          : "This point was flagged because multiple checks agreed it was not normal.";
    return `${valueText}The system flagged this point because ${causeText}. ${issueText}`;
  }

  const short = point.reason_short?.trim();
  if (short && !short.startsWith("[") && !short.includes("Checks that failed")) {
    return short;
  }
  const reason = point.reason?.trim();
  if (reason && !reason.startsWith("[") && reason.length < 280 && !reason.includes("Checks that failed")) {
    return reason;
  }
  if (point.s5_peer_fired === true) {
    return "This tag did not move like similar tags — likely a sensor or local control issue.";
  }
  if (point.s5_peer_fired === false) {
    return "This tag moved with related tags — likely a wider process change.";
  }
  return "This reading was flagged as unusual.";
}

export function failedEnginesForPoint(point: ResultPoint): FailedEngine[] {
  if (point.engines_fired?.length) return point.engines_fired;
  return [];
}
