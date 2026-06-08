import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { usePlantAnalysisStore } from "@/store/plantAnalysisStore";

interface SubsystemDropdownProps {
  plantId: string;
  value: string;
  onChange: (subsystem: string) => void;
}

export function SubsystemDropdown({ plantId, value, onChange }: SubsystemDropdownProps) {
  const plant = usePlantAnalysisStore((state) =>
    state.plants.find((item) => item.id === plantId),
  );

  return (
    <div className="space-y-2">
      <Label htmlFor="subsystem-select">Select Subsystem</Label>
      <Select
        id="subsystem-select"
        value={value}
        disabled={!plant}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{plant ? "Choose a subsystem…" : "Select a plant first"}</option>
        {plant?.subsystems.map((subsystem) => (
          <option key={subsystem} value={subsystem}>
            {subsystem}
          </option>
        ))}
      </Select>
    </div>
  );
}
