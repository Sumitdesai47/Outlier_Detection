import { TopBar } from "@/components/layout/TopBar";
import { HelpAccordion } from "@/components/HelpAccordion";

export function HelpPage() {
  return (
    <div>
      <TopBar
        title="Help / User Guide"
        subtitle="Plain-language explanations for engineers and non-technical users."
      />
      <div className="p-6">
        <HelpAccordion />
      </div>
    </div>
  );
}
