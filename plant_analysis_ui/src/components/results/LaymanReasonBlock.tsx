import { useMemo } from "react";

export type ReasonSection = { title: string; bullets: string[] };

/** Parse `[Section title]` blocks produced by plant_analysis_layman_reason.py */
export function parseLaymanReasonSections(reason: string | null): ReasonSection[] {
  if (!reason?.trim()) return [];

  const sections: ReasonSection[] = [];
  const blocks = reason.split(/\n\n+/);

  for (const block of blocks) {
    const lines = block
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    if (!lines.length) continue;

    const headerMatch = lines[0].match(/^\[(.+)\]$/);
    if (!headerMatch) {
      sections.push({ title: "Details", bullets: lines });
      continue;
    }

    const bullets = lines
      .slice(1)
      .map((line) => line.replace(/^•\s*/, "").trim())
      .filter(Boolean);
    sections.push({ title: headerMatch[1], bullets });
  }

  return sections;
}

export function LaymanReasonBlock({ reason }: { reason: string | null }) {
  const sections = useMemo(() => parseLaymanReasonSections(reason), [reason]);

  if (!sections.length) {
    return <span className="text-muted-foreground">No explanation stored for this point.</span>;
  }

  return (
    <div className="max-w-2xl space-y-4">
      {sections.map((section) => (
        <div key={section.title} className="rounded-md border bg-muted/20 px-3 py-2.5">
          <p className="text-sm font-semibold text-foreground">{section.title}</p>
          <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-muted-foreground">
            {section.bullets.map((bullet) => (
              <li key={`${section.title}-${bullet.slice(0, 48)}`}>{bullet}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
