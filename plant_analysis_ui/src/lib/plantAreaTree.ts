import type { AnalysisRun } from "@/types/results";

export interface PlantAreaNode {
  plant: string;
  areas: string[];
}

export function buildPlantAreaTree(runs: AnalysisRun[]): PlantAreaNode[] {
  const map = new Map<string, Set<string>>();
  for (const run of runs) {
    if (!map.has(run.plant_name)) {
      map.set(run.plant_name, new Set());
    }
    map.get(run.plant_name)!.add(run.subsystem);
  }

  return [...map.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([plant, areas]) => ({
      plant,
      areas: [...areas].sort((a, b) => a.localeCompare(b)),
    }));
}

export function resolveRunForPlantArea(
  runs: AnalysisRun[],
  plant: string,
  area: string,
): AnalysisRun | undefined {
  const matches = runs.filter((r) => r.plant_name === plant && r.subsystem === area);
  if (!matches.length) return undefined;
  return [...matches].sort((a, b) => b.processed_at.localeCompare(a.processed_at))[0];
}
