import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation, useSearchParams } from "react-router-dom";
import { Activity, ChevronDown, ChevronRight } from "lucide-react";
import { buildPlantAreaTree } from "@/lib/plantAreaTree";
import { cn } from "@/lib/utils";
import { useLiveResultDashboardStore } from "@/store/liveResultDashboardStore";

export function LiveResultDashboardNav() {
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const runs = useLiveResultDashboardStore((s) => s.runs);
  const loadFilters = useLiveResultDashboardStore((s) => s.loadFilters);
  const [expanded, setExpanded] = useState(true);
  const [expandedPlants, setExpandedPlants] = useState<Set<string>>(new Set());

  const activePlant = searchParams.get("plant") ?? "";
  const activeArea = searchParams.get("area") ?? "";
  const onLive = location.pathname === "/results/live" || location.pathname === "/results/live/";
  const tree = useMemo(() => buildPlantAreaTree(runs), [runs]);

  useEffect(() => {
    void loadFilters();
  }, [loadFilters]);

  useEffect(() => {
    if (activePlant) {
      setExpandedPlants((prev) => new Set(prev).add(activePlant));
      setExpanded(true);
    }
  }, [activePlant]);

  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
          onLive && !activePlant
            ? "bg-primary text-primary-foreground shadow-sm"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        <Activity className="h-4 w-4 shrink-0" />
        <span className="flex-1 text-left">Live Result Dashboard</span>
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 opacity-70" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 opacity-70" />
        )}
      </button>

      {expanded ? (
        <div className="ml-3 space-y-1 border-l border-border pl-3">
          {tree.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">No live (V5) results yet</p>
          ) : null}
          {tree.map(({ plant, areas }) => {
            const plantOpen = expandedPlants.has(plant);
            return (
              <div key={plant} className="space-y-0.5">
                <button
                  type="button"
                  onClick={() =>
                    setExpandedPlants((prev) => {
                      const next = new Set(prev);
                      if (next.has(plant)) next.delete(plant);
                      else next.add(plant);
                      return next;
                    })
                  }
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground"
                >
                  {plantOpen ? (
                    <ChevronDown className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5" />
                  )}
                  <span className="truncate">{plant}</span>
                </button>
                {plantOpen
                  ? areas.map((area) => {
                      const isActive =
                        onLive && activePlant === plant && activeArea === area;
                      const to = `/results/live?plant=${encodeURIComponent(plant)}&area=${encodeURIComponent(area)}`;
                      return (
                        <NavLink
                          key={`${plant}-${area}`}
                          to={to}
                          className={cn(
                            "block truncate rounded-md px-3 py-2 text-sm transition-colors",
                            isActive
                              ? "bg-primary text-primary-foreground shadow-sm"
                              : "text-muted-foreground hover:bg-muted hover:text-foreground",
                          )}
                        >
                          {area}
                        </NavLink>
                      );
                    })
                  : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
