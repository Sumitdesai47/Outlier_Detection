import { MinMaxFilterCard } from "@/components/MinMaxFilterCard";
import { TagConditionBuilder } from "@/components/TagConditionBuilder";

export function FilterBuilder({ tags }: { tags: string[] }) {
  return (
    <div className="space-y-6">
      <MinMaxFilterCard tags={tags} />
      <TagConditionBuilder tags={tags} />
    </div>
  );
}
