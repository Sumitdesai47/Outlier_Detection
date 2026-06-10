import type { ModelTagEntry, ResultPoint, ResultSummary } from "@/types/results";

export function datasetTagsFromSummary(summary: ResultSummary | null): string[] {
  if (!summary?.dataset_tags?.length) return [];
  return summary.dataset_tags.filter(Boolean);
}

function normalizeTagKey(tag: string): string {
  return tag.trim().replace(/\+/g, " ");
}

function looksLikeStructuredBlob(value: string): boolean {
  const t = value.trim();
  return t.startsWith("{") || t.startsWith("[");
}

function parseLooseObjectString(text: string): ModelTagEntry | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{")) return null;
  try {
    return parseModelTagEntry(JSON.parse(trimmed));
  } catch {
    const tagMatch = trimmed.match(/['"]tag['"]\s*:\s*['"]([^'"]+)['"]/);
    const corrMatch = trimmed.match(/['"]corr['"]\s*:\s*([-\d.eE+]+)/);
    if (!tagMatch?.[1]) return null;
    const corr = corrMatch?.[1] != null && Number.isFinite(Number(corrMatch[1])) ? Number(corrMatch[1]) : null;
    return { tag: tagMatch[1], corr };
  }
}

export function parseModelTagEntry(entry: unknown): ModelTagEntry | null {
  if (entry == null) return null;

  if (typeof entry === "string") {
    const trimmed = entry.trim();
    if (!trimmed) return null;
    if (looksLikeStructuredBlob(trimmed)) {
      const fromJson = (() => {
        try {
          return parseModelTagEntry(JSON.parse(trimmed));
        } catch {
          return null;
        }
      })();
      if (fromJson?.tag) return fromJson;
      return parseLooseObjectString(trimmed);
    }
    return { tag: trimmed };
  }

  if (Array.isArray(entry)) {
    return null;
  }

  if (typeof entry === "object") {
    const obj = entry as Record<string, unknown>;
    const tag = String(obj.tag ?? obj.feature_name ?? obj.name ?? "").trim();
    if (!tag) return null;

    const rawCorr = obj.corr ?? obj.correlation ?? obj.abs_corr ?? obj.lag_correlation;
    const corr =
      rawCorr != null && Number.isFinite(Number(rawCorr)) ? Number(rawCorr) : null;

    return {
      tag,
      corr,
      model_importance:
        obj.model_importance != null && Number.isFinite(Number(obj.model_importance))
          ? Number(obj.model_importance)
          : null,
      group_id:
        obj.group_id != null && Number.isFinite(Number(obj.group_id))
          ? Number(obj.group_id)
          : null,
    };
  }

  return null;
}

function entriesForTag(
  map: Record<string, ModelTagEntry[] | string[]> | undefined,
  tag: string,
): ModelTagEntry[] | string[] | undefined {
  if (!map || !tag) return undefined;
  const direct = map[tag];
  if (direct?.length) return direct;
  const normalized = normalizeTagKey(tag);
  if (normalized !== tag && map[normalized]?.length) return map[normalized];
  const hit = Object.entries(map).find(([key]) => normalizeTagKey(key) === normalized);
  return hit?.[1];
}

function toModelTagEntries(raw: ModelTagEntry[] | string[] | undefined): ModelTagEntry[] {
  if (!raw?.length) return [];
  const seen = new Set<string>();
  const out: ModelTagEntry[] = [];
  for (const entry of raw) {
    const parsed = parseModelTagEntry(entry);
    if (!parsed?.tag || seen.has(parsed.tag)) continue;
    seen.add(parsed.tag);
    out.push(parsed);
  }
  return out.sort((a, b) => a.tag.localeCompare(b.tag));
}

export function modelTagsForTag(summary: ResultSummary | null, tag: string): ModelTagEntry[] {
  return toModelTagEntries(entriesForTag(summary?.x_variables_by_tag, tag));
}

function collectRelatedTagNames(
  related: unknown,
  primaryTag: string,
  names: Set<string>,
): void {
  if (related == null) return;
  if (typeof related === "string") {
    const trimmed = related.trim();
    if (!trimmed) return;
    if (looksLikeStructuredBlob(trimmed)) {
      const parsed = parseModelTagEntry(trimmed) ?? parseLooseObjectString(trimmed);
      if (parsed?.tag && parsed.tag !== primaryTag) names.add(parsed.tag);
      return;
    }
    if (trimmed !== primaryTag) names.add(trimmed);
    return;
  }
  if (Array.isArray(related)) {
    for (const item of related) collectRelatedTagNames(item, primaryTag, names);
    return;
  }
  if (typeof related === "object") {
    const parsed = parseModelTagEntry(related);
    if (parsed?.tag && parsed.tag !== primaryTag) names.add(parsed.tag);
  }
}

export function relatedTagsFromPoints(points: ResultPoint[], tag: string): ModelTagEntry[] {
  const byTag = new Map<string, ModelTagEntry>();
  for (const p of points) {
    if (p.tag_name !== tag) continue;
    for (const related of p.related_tags ?? []) {
      const parsed =
        parseModelTagEntry(related) ??
        (typeof related === "string" ? parseLooseObjectString(related) : null);
      if (parsed?.tag && parsed.tag !== tag) {
        const prev = byTag.get(parsed.tag);
        byTag.set(parsed.tag, {
          tag: parsed.tag,
          corr: parsed.corr ?? prev?.corr ?? null,
        });
        continue;
      }
      const names = new Set<string>();
      collectRelatedTagNames(related, tag, names);
      for (const name of names) {
        if (!byTag.has(name)) byTag.set(name, { tag: name });
      }
    }
  }
  return [...byTag.values()].sort((a, b) => a.tag.localeCompare(b.tag));
}

export function mergeModelTagEntries(...lists: Array<ModelTagEntry[] | undefined>): ModelTagEntry[] {
  const byTag = new Map<string, ModelTagEntry>();
  for (const list of lists) {
    for (const entry of list ?? []) {
      const parsed = parseModelTagEntry(entry);
      if (!parsed?.tag) continue;
      const prev = byTag.get(parsed.tag);
      byTag.set(parsed.tag, {
        tag: parsed.tag,
        corr: parsed.corr ?? prev?.corr ?? null,
        model_importance: parsed.model_importance ?? prev?.model_importance ?? null,
        group_id: parsed.group_id ?? prev?.group_id ?? null,
      });
    }
  }
  return [...byTag.values()].sort((a, b) => a.tag.localeCompare(b.tag));
}
