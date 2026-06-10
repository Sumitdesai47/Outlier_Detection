export type Direction = "both" | "up" | "down";

export type DurationOption = "full" | "3m" | "6m" | "1y" | "custom";

export type TagConditionOperator = ">" | "<" | "=" | "between";

export interface Plant {
  id: string;
  name: string;
  subsystems: string[];
}

export interface MinMaxFilter {
  id: string;
  tag: string;
  min: string;
  max: string;
}

export interface TagCondition {
  id: string;
  tag: string;
  operator: TagConditionOperator;
  value: string;
  valueTo?: string;
}

export interface ParsedDataset {
  fileName: string;
  rowCount: number;
  columnCount: number;
  columns: string[];
  previewRows: Record<string, unknown>[];
  timestampColumn: string | null;
  tagColumns: string[];
  numericColumns: string[];
  nonNumericColumns: string[];
}

export interface AnalysisDraft {
  plantId: string;
  subsystem: string;
  dataset: ParsedDataset | null;
  minMaxFilters: MinMaxFilter[];
  direction: Direction;
  tagConditions: TagCondition[];
  criticalTags: string[];
  duration: DurationOption;
  customStartDate: string;
  customEndDate: string;
  /** Day-by-day rolling (slow). Default off = full-dataset run (~2 min). */
  rollingAnalysis: boolean;
}

export interface SavedConfiguration {
  id: string;
  savedAt: string;
  plantName: string;
  subsystem: string;
  fileName: string;
  timestampColumn: string | null;
  tagColumnCount: number;
  direction: Direction;
  minMaxFilterCount: number;
  tagConditionCount: number;
  criticalTags: string[];
  duration: DurationOption;
  customStartDate?: string;
  customEndDate?: string;
}
