import { Link } from "react-router-dom";
import { ArrowRight, Database, Factory, FileStack, Settings2 } from "lucide-react";
import { TopBar } from "@/components/layout/TopBar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";

export function DashboardPage() {
  const plants = usePlantAnalysisStore((state) => state.plants);
  const savedConfigurations = usePlantAnalysisStore((state) => state.savedConfigurations);

  const totalSubsystems = plants.reduce((sum, plant) => sum + plant.subsystems.length, 0);
  const uploadedDatasets = savedConfigurations.length;
  const recentConfigurations = savedConfigurations.slice(0, 5);

  const stats = [
    { label: "Total plants", value: plants.length, icon: Factory },
    { label: "Total subsystems", value: totalSubsystems, icon: Settings2 },
    { label: "Uploaded datasets", value: uploadedDatasets, icon: Database },
    {
      label: "Recent saved configurations",
      value: savedConfigurations.length,
      icon: FileStack,
    },
  ];

  return (
    <div>
      <TopBar
        title="Dashboard / Home"
        subtitle="Prepare plant data and analysis settings in one simple workflow."
      />
      <div className="space-y-6 p-6">
        <Card className="border-primary/20 bg-gradient-to-r from-primary/10 via-card to-card">
          <CardContent className="flex flex-col gap-4 p-6 md:flex-row md:items-center md:justify-between">
            <div className="max-w-2xl">
              <h3 className="text-xl font-semibold">Industrial Plant Data Analysis</h3>
              <p className="mt-2 text-sm text-muted-foreground">
                A clean workspace for chemical engineers and operations teams to upload
                historical plant data, define filters, and save analysis configurations —
                without touching complex model settings.
              </p>
            </div>
            <div className="flex flex-wrap gap-2 shrink-0">
              <Link to="/upload-configure">
                <Button className="inline-flex items-center gap-2">
                  Start New Analysis
                  <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
              <Link to="/results">
                <Button variant="outline" className="inline-flex items-center gap-2">
                  View Result Dashboard
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {stats.map(({ label, value, icon: Icon }) => (
            <Card key={label}>
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
                <Icon className="h-4 w-4 text-primary" />
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold">{value}</p>
              </CardContent>
            </Card>
          ))}
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Recent Saved Configurations</CardTitle>
          </CardHeader>
          <CardContent>
            {recentConfigurations.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No configurations saved yet. Start a new analysis to create your first setup.
              </p>
            ) : (
              <div className="space-y-3">
                {recentConfigurations.map((config) => (
                  <div
                    key={config.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-lg border px-4 py-3"
                  >
                    <div>
                      <p className="text-sm font-medium">
                        {config.plantName} · {config.subsystem}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {config.fileName} · {new Date(config.savedAt).toLocaleString()}
                      </p>
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {config.tagColumnCount} tags · {config.criticalTags.length} critical
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
