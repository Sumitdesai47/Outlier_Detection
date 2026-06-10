import { create } from "zustand";

import { resolveRunForPlantArea } from "@/lib/plantAreaTree";

import {

  fetchFilterOptions,

  fetchObservationDays,

  fetchPoints,

  fetchSeries,

  fetchSummary,

  fetchTagContext,

} from "@/lib/resultsApi";

import { sortByTimestampAsc } from "@/lib/sortResults";

import type {

  AnalysisRun,

  ResultFilters,

  ResultPoint,

  ResultSummary,

  ResultTab,

  RunDayMeta,

  TagContext,

} from "@/types/results";



interface ResultDashboardState {

  activeTab: ResultTab;

  filters: ResultFilters;

  runs: AnalysisRun[];

  availableTags: string[];

  compareTags: string[];

  tagContext: TagContext | null;

  dayMeta: RunDayMeta | null;

  summary: ResultSummary | null;

  points: ResultPoint[];

  seriesPoints: ResultPoint[];

  loading: boolean;

  error: string | null;

  setActiveTab: (tab: ResultTab) => void;

  setSelectedDay: (day: string) => void;

  setFilter: (patch: Partial<ResultFilters>) => void;

  toggleCompareTag: (tag: string) => void;

  setCompareTags: (tags: string[]) => void;

  clearCompareTags: () => void;

  fetchObservationDays: () => Promise<void>;

  loadFilters: () => Promise<void>;

  loadDashboard: () => Promise<void>;

  loadSeries: () => Promise<void>;

}



const emptyFilters = (): ResultFilters => ({

  plant: "",

  subsystem: "",

  runId: "",

  tag: "",

  selectedDay: "",

});



export const useResultDashboardStore = create<ResultDashboardState>((set, get) => ({

  activeTab: "summary",

  filters: emptyFilters(),

  runs: [],

  availableTags: [],

  compareTags: [],

  tagContext: null,

  dayMeta: null,

  summary: null,

  points: [],

  seriesPoints: [],

  loading: false,

  error: null,



  setActiveTab: (tab) => {

    set({ activeTab: tab, compareTags: [] });

    void get().loadDashboard();

  },



  setSelectedDay: (day) => {

    set((state) => ({ filters: { ...state.filters, selectedDay: day } }));

    void get().loadDashboard();

  },



  setFilter: (patch) => {

    const state = get();

    const next = { ...state.filters, ...patch };

    let runChanged = false;

    const tagChanged = patch.tag !== undefined && patch.tag !== state.filters.tag;



    if (patch.plant !== undefined || patch.subsystem !== undefined) {

      const run = resolveRunForPlantArea(state.runs, next.plant, next.subsystem);

      if (run) {

        runChanged = next.runId !== run.id;

        next.runId = run.id;

      }

    }



    if (patch.runId !== undefined) {

      runChanged = next.runId !== patch.runId;

      const run = state.runs.find((item) => item.id === patch.runId);

      if (run) {

        next.plant = run.plant_name;

        next.subsystem = run.subsystem;

      }

    }



    if (runChanged || tagChanged) {

      next.selectedDay = runChanged ? "" : next.selectedDay;

      set({ dayMeta: runChanged ? null : state.dayMeta, compareTags: [], tagContext: null });

    }



    set({ filters: next });

    void get().loadDashboard();

  },



  toggleCompareTag: (tag) => {

    const current = get().compareTags;

    const next = current.includes(tag) ? current.filter((t) => t !== tag) : [...current, tag];

    set({ compareTags: next });

    void get().loadSeries();

  },



  setCompareTags: (tags) => {

    set({ compareTags: tags });

    void get().loadSeries();

  },



  clearCompareTags: () => {

    set({ compareTags: [] });

    void get().loadSeries();

  },



  fetchObservationDays: async () => {

    const { filters } = get();

    if (!filters.runId) {

      set({ dayMeta: null });

      return;

    }

    try {

      const dayMeta = await fetchObservationDays(filters.runId);

      const isRolling = dayMeta.methodology === "rolling_expanding";

      const selectedDay =

        filters.selectedDay && dayMeta.observation_days.includes(filters.selectedDay)

          ? filters.selectedDay

          : isRolling

            ? ""

            : dayMeta.selected_day || dayMeta.observation_last || "";

      set((state) => ({

        dayMeta,

        filters: { ...state.filters, selectedDay },

      }));

    } catch {

      set({ dayMeta: null });

    }

  },



  loadSeries: async () => {
    const { filters, compareTags } = get();
    if (!filters.runId || !filters.tag) return;
    try {
      const seriesResult = await fetchSeries({
        runId: filters.runId,
        tag: filters.tag,
        compare: compareTags,
      });
      set({ seriesPoints: sortByTimestampAsc(seriesResult.points) });
    } catch {
      /* keep prior series on compare-only failure */
    }
  },



  loadFilters: async () => {

    try {

      const data = await fetchFilterOptions();

      const runs = data.runs.filter(

        (run) => String((run as AnalysisRun & { summary?: { engine?: string } }).summary?.engine || "multimodel_outlier") !== "live_outlier",

      );

      const current = get().filters;

      let plant = current.plant;

      let subsystem = current.subsystem;

      let runId = current.runId;



      if (runId) {

        const run = runs.find((item) => item.id === runId);

        if (run) {

          plant = run.plant_name;

          subsystem = run.subsystem;

        }

      } else if (plant && subsystem) {

        runId = resolveRunForPlantArea(runs, plant, subsystem)?.id || "";

      }



      set({

        runs,

        availableTags: data.tags,

        filters: {

          ...current,

          plant,

          subsystem,

          runId,

          tag: current.tag || data.tags[0] || "",

          selectedDay: current.selectedDay || "",

        },

      });

      if (runId) {

        await get().loadDashboard();

      }

    } catch (error) {

      set({ error: error instanceof Error ? error.message : "Failed to load results navigation" });

    }

  },



  loadDashboard: async () => {

    const { filters, activeTab, compareTags } = get();

    if (!filters.runId) return;

    set({ loading: true, error: null });

    try {

      await get().fetchObservationDays();

      const current = get();

      const selectedDay = current.filters.selectedDay || undefined;

      const summary = await fetchSummary(filters.runId);

      let points: ResultPoint[] = [];

      let seriesPoints: ResultPoint[] = [];

      let tags = get().availableTags;

      let tagContext: TagContext | null = null;



      if (activeTab === "summary") {

        seriesPoints = [];

      } else {

        const tabResult = await fetchPoints({

          runId: filters.runId,

          tab: activeTab,

          tag: filters.tag || undefined,

          dateFrom: selectedDay,

        });

        points = tabResult.points;

        tags = tabResult.tags.length ? tabResult.tags : tags;



        const nextTag =

          filters.tag && tags.includes(filters.tag) ? filters.tag : tags[0] ?? "";



        if (nextTag) {

          try {

            tagContext = await fetchTagContext(filters.runId, nextTag);

          } catch {

            tagContext = null;

          }

        }



        const seriesResult = await fetchSeries({
          runId: filters.runId,
          tag: nextTag,
          compare: compareTags,
        });

        seriesPoints = seriesResult.points;

      }



      const nextTag =

        filters.tag && tags.includes(filters.tag) ? filters.tag : tags[0] ?? "";



      set({

        summary,

        points: sortByTimestampAsc(points),

        seriesPoints: sortByTimestampAsc(seriesPoints),

        availableTags: tags,

        tagContext,

        filters: { ...filters, tag: activeTab === "summary" ? filters.tag : nextTag },

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


