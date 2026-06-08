import { create } from "zustand";
import { resolveRunForPlantArea } from "@/lib/plantAreaTree";
import { fetchFilterOptions, fetchPoints, fetchSeries, fetchSummary } from "@/lib/resultsApi";
import { sortByTimestampAsc } from "@/lib/sortResults";
import type { AnalysisRun, ResultFilters, ResultPoint, ResultSummary, ResultTab } from "@/types/results";

interface ResultDashboardState {
  activeTab: ResultTab;
  filters: ResultFilters;
  runs: AnalysisRun[];
  availableTags: string[];
  summary: ResultSummary | null;
  points: ResultPoint[];
  seriesPoints: ResultPoint[];
  loading: boolean;
  error: string | null;
  setActiveTab: (tab: ResultTab) => void;
  setFilter: (patch: Partial<ResultFilters>) => void;
  loadFilters: () => Promise<void>;
  loadDashboard: () => Promise<void>;
}

const emptyFilters = (): ResultFilters => ({
  plant: "",
  subsystem: "",
  runId: "",
  tag: "",
});

export const useResultDashboardStore = create<ResultDashboardState>((set, get) => ({
  activeTab: "summary",
  filters: emptyFilters(),
  runs: [],
  availableTags: [],
  summary: null,
  points: [],
  seriesPoints: [],
  loading: false,
  error: null,

  setActiveTab: (tab) => {
    set({ activeTab: tab });
    void get().loadDashboard();
  },

  setFilter: (patch) => {
    const state = get();
    const next = { ...state.filters, ...patch };

    if (patch.plant !== undefined || patch.subsystem !== undefined) {
      const run = resolveRunForPlantArea(state.runs, next.plant, next.subsystem);
      if (run) {
        next.runId = run.id;
      }
    }

    if (patch.runId !== undefined) {
      const run = state.runs.find((item) => item.id === patch.runId);
      if (run) {
        next.plant = run.plant_name;
        next.subsystem = run.subsystem;
      }
    }

    set({ filters: next });
    void get().loadDashboard();
  },

  loadFilters: async () => {
    try {
      const data = await fetchFilterOptions();
      const current = get().filters;
      let plant = current.plant;
      let subsystem = current.subsystem;
      let runId = current.runId;

      if (runId) {
        const run = data.runs.find((item) => item.id === runId);
        if (run) {
          plant = run.plant_name;
          subsystem = run.subsystem;
        }
      } else if (plant && subsystem) {
        runId = resolveRunForPlantArea(data.runs, plant, subsystem)?.id || "";
      }

      set({
        runs: data.runs,
        availableTags: data.tags,
        filters: {
          ...current,
          plant,
          subsystem,
          runId,
          tag: current.tag || data.tags[0] || "",
        },
      });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Failed to load results navigation" });
    }
  },

  loadDashboard: async () => {
    const { filters, activeTab } = get();
    if (!filters.runId) return;
    set({ loading: true, error: null });
    try {
      const summary = await fetchSummary(filters.runId);
      let points: ResultPoint[] = [];
      let seriesPoints: ResultPoint[] = [];
      let tags = get().availableTags;

      if (activeTab === "summary") {
        seriesPoints = [];
      } else {
        const tabResult = await fetchPoints({
          runId: filters.runId,
          tab: activeTab,
          tag: filters.tag || undefined,
        });
        points = tabResult.points;
        tags = tabResult.tags.length ? tabResult.tags : tags;

        const seriesResult = await fetchSeries({
          runId: filters.runId,
          tag: filters.tag || undefined,
        });
        seriesPoints = seriesResult.points;
      }

      set({
        summary,
        points: sortByTimestampAsc(points),
        seriesPoints: sortByTimestampAsc(seriesPoints),
        availableTags: tags,
        loading: false,
      });
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : "Failed to load results",
      });
    }
  },
}));
