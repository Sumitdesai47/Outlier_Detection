import { create } from "zustand";
import { persist } from "zustand/middleware";
import { uid } from "@/lib/utils";
import type {
  AnalysisDraft,
  Direction,
  DurationOption,
  MinMaxFilter,
  Plant,
  SavedConfiguration,
  TagCondition,
} from "@/types";

const DEFAULT_SUBSYSTEMS = [
  "Furnace",
  "Compressor",
  "Reactor",
  "Heat Exchanger",
  "Distillation Column",
];

const initialPlants: Plant[] = [
  {
    id: "plant-1",
    name: "Plant 1",
    subsystems: [...DEFAULT_SUBSYSTEMS],
  },
  {
    id: "plant-2",
    name: "Plant 2",
    subsystems: ["Furnace", "Compressor", "Reactor"],
  },
];

const emptyDraft = (): AnalysisDraft => ({
  plantId: "",
  subsystem: "",
  dataset: null,
  minMaxFilters: [],
  direction: "both",
  tagConditions: [],
  criticalTags: [],
  duration: "full",
  customStartDate: "",
  customEndDate: "",
  rollingAnalysis: false,
});

interface PlantAnalysisState {
  plants: Plant[];
  savedConfigurations: SavedConfiguration[];
  draft: AnalysisDraft;
  addPlant: (name: string) => void;
  updatePlantName: (plantId: string, name: string) => void;
  deletePlant: (plantId: string) => void;
  addSubsystem: (plantId: string, subsystem: string) => void;
  removeSubsystem: (plantId: string, subsystem: string) => void;
  setDraftPlant: (plantId: string) => void;
  setDraftSubsystem: (subsystem: string) => void;
  setDraftDataset: (dataset: AnalysisDraft["dataset"]) => void;
  addMinMaxFilter: () => void;
  updateMinMaxFilter: (id: string, patch: Partial<MinMaxFilter>) => void;
  removeMinMaxFilter: (id: string) => void;
  setDirection: (direction: Direction) => void;
  addTagCondition: () => void;
  updateTagCondition: (id: string, patch: Partial<TagCondition>) => void;
  removeTagCondition: (id: string) => void;
  toggleCriticalTag: (tag: string) => void;
  setDuration: (duration: DurationOption) => void;
  setCustomDateRange: (start: string, end: string) => void;
  setRollingAnalysis: (enabled: boolean) => void;
  saveConfiguration: () => SavedConfiguration | null;
  resetDraft: () => void;
}

export const usePlantAnalysisStore = create<PlantAnalysisState>()(
  persist(
    (set, get) => ({
      plants: initialPlants,
      savedConfigurations: [],
      draft: emptyDraft(),

      addPlant: (name) => {
        const trimmed = name.trim();
        if (!trimmed) return;
        set((state) => ({
          plants: [
            ...state.plants,
            {
              id: uid("plant"),
              name: trimmed,
              subsystems: [...DEFAULT_SUBSYSTEMS],
            },
          ],
        }));
      },

      updatePlantName: (plantId, name) => {
        const trimmed = name.trim();
        if (!trimmed) return;
        set((state) => ({
          plants: state.plants.map((plant) =>
            plant.id === plantId ? { ...plant, name: trimmed } : plant,
          ),
        }));
      },

      deletePlant: (plantId) => {
        set((state) => ({
          plants: state.plants.filter((plant) => plant.id !== plantId),
          draft:
            state.draft.plantId === plantId
              ? { ...state.draft, plantId: "", subsystem: "" }
              : state.draft,
        }));
      },

      addSubsystem: (plantId, subsystem) => {
        const trimmed = subsystem.trim();
        if (!trimmed) return;
        set((state) => ({
          plants: state.plants.map((plant) =>
            plant.id === plantId && !plant.subsystems.includes(trimmed)
              ? { ...plant, subsystems: [...plant.subsystems, trimmed] }
              : plant,
          ),
        }));
      },

      removeSubsystem: (plantId, subsystem) => {
        set((state) => ({
          plants: state.plants.map((plant) =>
            plant.id === plantId
              ? {
                  ...plant,
                  subsystems: plant.subsystems.filter((item) => item !== subsystem),
                }
              : plant,
          ),
          draft:
            state.draft.plantId === plantId && state.draft.subsystem === subsystem
              ? { ...state.draft, subsystem: "" }
              : state.draft,
        }));
      },

      setDraftPlant: (plantId) =>
        set((state) => ({
          draft: { ...state.draft, plantId, subsystem: "" },
        })),

      setDraftSubsystem: (subsystem) =>
        set((state) => ({
          draft: { ...state.draft, subsystem },
        })),

      setDraftDataset: (dataset) =>
        set((state) => ({
          draft: {
            ...state.draft,
            dataset,
            minMaxFilters: [],
            tagConditions: [],
            criticalTags: [],
          },
        })),

      addMinMaxFilter: () =>
        set((state) => ({
          draft: {
            ...state.draft,
            minMaxFilters: [
              ...state.draft.minMaxFilters,
              { id: uid("mm"), tag: "", min: "", max: "" },
            ],
          },
        })),

      updateMinMaxFilter: (id, patch) =>
        set((state) => ({
          draft: {
            ...state.draft,
            minMaxFilters: state.draft.minMaxFilters.map((filter) =>
              filter.id === id ? { ...filter, ...patch } : filter,
            ),
          },
        })),

      removeMinMaxFilter: (id) =>
        set((state) => ({
          draft: {
            ...state.draft,
            minMaxFilters: state.draft.minMaxFilters.filter((filter) => filter.id !== id),
          },
        })),

      setDirection: (direction) =>
        set((state) => ({
          draft: { ...state.draft, direction },
        })),

      addTagCondition: () =>
        set((state) => ({
          draft: {
            ...state.draft,
            tagConditions: [
              ...state.draft.tagConditions,
              { id: uid("tc"), tag: "", operator: ">", value: "" },
            ],
          },
        })),

      updateTagCondition: (id, patch) =>
        set((state) => ({
          draft: {
            ...state.draft,
            tagConditions: state.draft.tagConditions.map((condition) =>
              condition.id === id ? { ...condition, ...patch } : condition,
            ),
          },
        })),

      removeTagCondition: (id) =>
        set((state) => ({
          draft: {
            ...state.draft,
            tagConditions: state.draft.tagConditions.filter((condition) => condition.id !== id),
          },
        })),

      toggleCriticalTag: (tag) =>
        set((state) => {
          const exists = state.draft.criticalTags.includes(tag);
          return {
            draft: {
              ...state.draft,
              criticalTags: exists
                ? state.draft.criticalTags.filter((item) => item !== tag)
                : [...state.draft.criticalTags, tag],
            },
          };
        }),

      setDuration: (duration) =>
        set((state) => ({
          draft: { ...state.draft, duration },
        })),

      setCustomDateRange: (start, end) =>
        set((state) => ({
          draft: {
            ...state.draft,
            customStartDate: start,
            customEndDate: end,
          },
        })),

      setRollingAnalysis: (enabled) =>
        set((state) => ({
          draft: {
            ...state.draft,
            rollingAnalysis: enabled,
            duration: enabled ? "full" : state.draft.duration,
          },
        })),

      saveConfiguration: () => {
        const state = get();
        const { draft, plants } = state;
        const plant = plants.find((item) => item.id === draft.plantId);
        if (!plant || !draft.subsystem || !draft.dataset) return null;

        const saved: SavedConfiguration = {
          id: uid("cfg"),
          savedAt: new Date().toISOString(),
          plantName: plant.name,
          subsystem: draft.subsystem,
          fileName: draft.dataset.fileName,
          timestampColumn: draft.dataset.timestampColumn,
          tagColumnCount: draft.dataset.tagColumns.length,
          direction: draft.direction,
          minMaxFilterCount: draft.minMaxFilters.filter((f) => f.tag).length,
          tagConditionCount: draft.tagConditions.filter((c) => c.tag && c.value).length,
          criticalTags: draft.criticalTags,
          duration: draft.duration,
          customStartDate: draft.customStartDate || undefined,
          customEndDate: draft.customEndDate || undefined,
        };

        set((current) => ({
          savedConfigurations: [saved, ...current.savedConfigurations],
        }));

        return saved;
      },

      resetDraft: () => set({ draft: emptyDraft() }),
    }),
    {
      name: "plant-analysis-store",
      partialize: (state) => ({
        plants: state.plants,
        savedConfigurations: state.savedConfigurations,
        draft: state.draft,
      }),
    },
  ),
);
