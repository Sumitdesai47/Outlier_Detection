import { BarChart3 } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

export function EmptyState({
  title = "No results yet",
  description = "Run an analysis from Upload & Configure to populate this dashboard.",
}: {
  title?: string;
  description?: string;
}) {
  return (
    <div className="flex min-h-[280px] flex-col items-center justify-center rounded-lg border bg-muted/20 p-8 text-center">
      <BarChart3 className="mb-3 h-10 w-10 text-muted-foreground" />
      <h3 className="text-lg font-semibold">{title}</h3>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">{description}</p>
      <Link to="/upload-configure" className="mt-4">
        <Button>Go to Upload & Configure</Button>
      </Link>
    </div>
  );
}
