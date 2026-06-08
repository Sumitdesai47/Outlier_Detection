import { NavLink } from "react-router-dom";
import {
  Factory,
  HelpCircle,
  LayoutDashboard,
  UploadCloud,
} from "lucide-react";
import { ResultDashboardNav } from "@/components/layout/ResultDashboardNav";
import { cn } from "@/lib/utils";

const links = [
  { to: "/", label: "Dashboard / Home", icon: LayoutDashboard },
  { to: "/plants", label: "Plants", icon: Factory },
  { to: "/upload-configure", label: "Upload & Configure Analysis", icon: UploadCloud },
  { to: "/help", label: "Help / User Guide", icon: HelpCircle },
];

export function AppSidebar() {
  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r bg-card">
      <div className="border-b px-6 py-5">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          Process Intelligence
        </p>
        <h1 className="mt-1 text-lg font-semibold text-foreground">Plant Analysis</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          Industrial data setup workspace
        </p>
      </div>
      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto p-4">
        {links.slice(0, 3).map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                isActive
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )
            }
          >
            <Icon className="h-4 w-4 shrink-0" />
            <span>{label}</span>
          </NavLink>
        ))}

        <ResultDashboardNav />

        {links.slice(3).map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                isActive
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )
            }
          >
            <Icon className="h-4 w-4 shrink-0" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="border-t px-4 py-4 text-xs text-muted-foreground">
        Upload, configure, and review classified results.
      </div>
    </aside>
  );
}
