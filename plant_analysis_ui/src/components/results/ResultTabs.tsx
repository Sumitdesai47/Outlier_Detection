import { cn } from "@/lib/utils";
import type { ResultTab } from "@/types/results";

const tabs: { id: ResultTab; label: string; helper: string }[] = [
  {
    id: "summary",
    label: "Overall Results Summary",
    helper: "High-level KPIs and tag-wise status distribution.",
  },
  {
    id: "outlier",
    label: "Tag issue",
    helper:
      "Outlier detected and S5 peer engine failed — the tag diverges from related peer tags.",
  },
  {
    id: "process",
    label: "Process issue",
    helper:
      "Outlier detected and S5 peer engine passed — pattern aligns with wider process movement.",
  },
  {
    id: "both",
    label: "Both",
    helper: "All abnormal points — tag issue and process issue combined.",
  },
];

export function ResultTabs({
  activeTab,
  onChange,
}: {
  activeTab: ResultTab;
  onChange: (tab: ResultTab) => void;
}) {
  const active = tabs.find((t) => t.id === activeTab);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 border-b pb-2">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => onChange(tab.id)}
            className={cn(
              "rounded-lg px-4 py-2 text-sm font-medium transition-colors",
              activeTab === tab.id
                ? "bg-primary text-primary-foreground"
                : "bg-muted/40 text-muted-foreground hover:bg-muted",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {active ? <p className="text-sm text-muted-foreground">{active.helper}</p> : null}
    </div>
  );
}
