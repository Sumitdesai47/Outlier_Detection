import { create } from "zustand";
import { resolveRunForPlantArea } from "@/lib/plantAreaTree";
import { fetchLiveOverview, fetchLiveRuns, fetchLiveTagDetail } from "@/lib/liveResultsApi";
import type { LiveDashboardOverview, LiveTagDetail } from "@/types/liveResults";
import type { AnalysisRun, ResultFilters } from "@/types/results";

interface LiveResultDashboardState {
  filters: ResultFilters;
  runs: AnalysisRun[];
  overview: LiveDashboardOverview | null;
  detail: LiveTagDetail | null;
  selectedTag: string;
  compareTags: string[];
  loading: boolean;
  plotLoading: boolean;
  error: string | null;
  setFilter: (patch: Partial<ResultFilters>) => void;
  setSelectedDay: (day: string) => void;
  setSelectedTag: (tag: string) => void;
  toggleCompareTag: (tag: string) => void;
  clearCompareTags: () => void;
  loadFilters: () => Promise<void>;
  loadDashboard: () => Promise<void>;
  loadTagDetail: () => Promise<void>;
}

const emptyFilters = (): ResultFilters => ({
  plant: "",
  subsystem: "",
  runId: "",
  tag: "",
  selectedDay: "",
});

export const useLiveResultDashboardStore = create<LiveResultDashboardState>((set, get) => ({
  filters: emptyFilters(),
  runs: [],
  overview: null,
  detail: null,
  selectedTag: "",
  compareTags: [],
  loading: false,
  plotLoading: false,
  error: null,

  setFilter: (patch) => {
    const state = get();
    const next = { ...state.filters, ...patch };
    if (patch.runId) {
      const run = state.runs.find((item) => item.id === patch.runId);
      if (run) {
        next.plant = run.plant_name;
        next.subsystem = run.subsystem;
      }
    } else if (patch.plant !== undefined || patch.subsystem !== undefined) {
      const run = resolveRunForPlantArea(state.runs, next.plant, next.subsystem);
      if (run) next.runId = run.id;
    }
    set({ filters: next });
    void get().loadDashboard();
  },

  setSelectedDay: (day) => {
    const { overview } = get();
    if (!overview) return;
    set({ overview: { ...overview, selected_day: day }, selectedTag: "", compareTags: [] });
    void get().loadDashboard();
  },

  setSelectedTag: (tag) => {
    set({ selectedTag: tag, compareTags: [] });
    void get().loadTagDetail();
  },

  toggleCompareTag: (tag) => {
    const current = get().compareTags;
    const next = current.includes(tag)
      ? current.filter((t) => t !== tag)
      : [...current, tag];
    set({ compareTags: next });
    void get().loadTagDetail();
  },

  clearCompareTags: () => {
    set({ compareTags: [] });
    void get().loadTagDetail();
  },

  loadFilters: async () => {
    const runs = await fetchLiveRuns();
    const current = get().filters;
    let plant = current.plant;
    let subsystem = current.subsystem;
    let runId = current.runId;
    if (runId) {
      const run = runs.find((r) => r.id === runId);
      if (run) {
        plant = run.plant_name;
        subsystem = run.subsystem;
      }
    } else if (plant && subsystem) {
      runId = resolveRunForPlantArea(runs, plant, subsystem)?.id || "";
    }
    set({ runs, filters: { ...current, plant, subsystem, runId } });
  },

  loadDashboard: async () => {
    const { filters } = get();
    if (!filters.runId) return;
    set({ loading: true, error: null });
    try {
      const overview = await fetchLiveOverview({
        runId: filters.runId,
        day: get().overview?.selected_day ?? undefined,
      });
      if (overview.error) {
        set({ overview, loading: false, error: overview.error, detail: null });
        return;
      }
      let selectedTag = get().selectedTag;
      if (!selectedTag && overview.drifts.length) {
        selectedTag = overview.drifts[0].tag;
      }
      set({ overview, selectedTag, loading: false, error: null });
      if (selectedTag && overview.selected_day) {
        void get().loadTagDetail();
      }
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : "Failed to load live dashboard",
      });
    }
  },

  loadTagDetail: async () => {
    const { filters, overview, selectedTag, compareTags } = get();
    if (!filters.runId || !overview?.selected_day || !selectedTag) return;
    set({ plotLoading: true });
    try {
      const detail = await fetchLiveTagDetail({
        runId: filters.runId,
        day: overview.selected_day,
        tag: selectedTag,
        compare: compareTags,
      });
      set({ detail, plotLoading: false });
    } catch (error) {
      set({
        plotLoading: false,
        error: error instanceof Error ? error.message : "Failed to load plot",
      });
    }
  },
}));
