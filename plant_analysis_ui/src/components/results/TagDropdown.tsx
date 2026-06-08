import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

export function TagDropdown({
  value,
  tags,
  onChange,
}: {
  value: string;
  tags: string[];
  onChange: (tag: string) => void;
}) {
  return (
    <div className="space-y-2">
      <Label>Select tag</Label>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All tags</option>
        {tags.map((tag) => (
          <option key={tag} value={tag}>
            {tag}
          </option>
        ))}
      </Select>
    </div>
  );
}
