import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";

interface PlantDropdownProps {
  value: string;
  onChange: (plantId: string) => void;
}

export function PlantDropdown({ value, onChange }: PlantDropdownProps) {
  const plants = usePlantAnalysisStore((state) => state.plants);

  return (
    <div className="space-y-2">
      <Label htmlFor="plant-select">Select Plant Name</Label>
      <Select
        id="plant-select"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Choose a plant…</option>
        {plants.map((plant) => (
          <option key={plant.id} value={plant.id}>
            {plant.name}
          </option>
        ))}
      </Select>
    </div>
  );
}
