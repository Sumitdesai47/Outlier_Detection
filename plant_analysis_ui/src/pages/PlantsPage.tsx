import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { TopBar } from "@/components/layout/TopBar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";

export function PlantsPage() {
  const plants = usePlantAnalysisStore((state) => state.plants);
  const addPlant = usePlantAnalysisStore((state) => state.addPlant);
  const updatePlantName = usePlantAnalysisStore((state) => state.updatePlantName);
  const deletePlant = usePlantAnalysisStore((state) => state.deletePlant);
  const addSubsystem = usePlantAnalysisStore((state) => state.addSubsystem);
  const removeSubsystem = usePlantAnalysisStore((state) => state.removeSubsystem);

  const [newPlantName, setNewPlantName] = useState("");
  const [subsystemDrafts, setSubsystemDrafts] = useState<Record<string, string>>({});

  return (
    <div>
      <TopBar
        title="Plants"
        subtitle="Organize production sites and the equipment areas underneath each plant."
      />
      <div className="space-y-6 p-6">
        <Card>
          <CardHeader>
            <CardTitle>Add New Plant</CardTitle>
            <CardDescription>Create a plant entry before uploading subsystem data.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 sm:flex-row">
            <Input
              placeholder="Plant name, e.g. Plant 3"
              value={newPlantName}
              onChange={(event) => setNewPlantName(event.target.value)}
            />
            <Button
              onClick={() => {
                addPlant(newPlantName);
                setNewPlantName("");
              }}
            >
              <Plus className="h-4 w-4" />
              Add plant
            </Button>
          </CardContent>
        </Card>

        <div className="grid gap-4 xl:grid-cols-2">
          {plants.map((plant) => (
            <Card key={plant.id}>
              <CardHeader>
                <CardTitle className="flex items-center justify-between gap-3">
                  <Input
                    defaultValue={plant.name}
                    onBlur={(event) => updatePlantName(plant.id, event.target.value)}
                    className="max-w-xs font-semibold"
                  />
                  <Button
                    variant="ghost"
                    className="text-destructive"
                    onClick={() => deletePlant(plant.id)}
                  >
                    <Trash2 className="h-4 w-4" />
                    Delete
                  </Button>
                </CardTitle>
                <CardDescription>Manage subsystems for this plant.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {plant.subsystems.map((subsystem) => (
                    <span
                      key={subsystem}
                      className="inline-flex items-center gap-2 rounded-full border bg-muted/30 px-3 py-1 text-xs"
                    >
                      {subsystem}
                      <button
                        type="button"
                        className="text-muted-foreground hover:text-destructive"
                        onClick={() => removeSubsystem(plant.id, subsystem)}
                        aria-label={`Remove ${subsystem}`}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
                <div className="flex gap-2">
                  <Input
                    placeholder="Add subsystem, e.g. Heat Exchanger"
                    value={subsystemDrafts[plant.id] ?? ""}
                    onChange={(event) =>
                      setSubsystemDrafts((current) => ({
                        ...current,
                        [plant.id]: event.target.value,
                      }))
                    }
                  />
                  <Button
                    variant="outline"
                    onClick={() => {
                      addSubsystem(plant.id, subsystemDrafts[plant.id] ?? "");
                      setSubsystemDrafts((current) => ({ ...current, [plant.id]: "" }));
                    }}
                  >
                    Add
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
