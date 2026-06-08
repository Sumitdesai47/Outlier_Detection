import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const items = [
  {
    title: "What is a plant?",
    body: "A plant is a physical production site, such as Plant 1 or Plant 2. Grouping data by plant keeps analysis organized by location.",
    example: "Example: Plant 1 contains furnace and compressor data from one refinery unit.",
  },
  {
    title: "What is a subsystem?",
    body: "A subsystem is a major equipment area inside a plant, like a furnace, compressor, or reactor.",
    example: "Example: Select Furnace when uploading temperature and pressure tags from the furnace area.",
  },
  {
    title: "What is a timestamp column?",
    body: "A timestamp column records when each measurement was taken. The app looks for columns named Time, Date, or Timestamp.",
    example: "Example: 2024-06-01 08:00:00",
  },
  {
    title: "What is a tag / sensor column?",
    body: "Tag columns are numeric sensor readings such as temperature, pressure, or flow.",
    example: "Example: DOL, MFI, REACTOR_TEMP",
  },
  {
    title: "What is a min–max filter?",
    body: "Min–max filters remove values outside normal operating limits before analysis.",
    example: "Example: Keep REACTOR_TEMP between 200 and 350.",
  },
  {
    title: "What is direction selection?",
    body: "Direction tells the system whether to focus on increases, decreases, or both.",
    example: "Example: Choose Up to watch for sudden temperature rises.",
  },
  {
    title: "What are tag filter conditions?",
    body: "Tag conditions keep only rows that match process rules.",
    example: "Example: DOL > 10 and MFI between 20 and 80",
  },
  {
    title: "What are critical tags?",
    body: "Critical tags are the most important sensors you want prioritized during monitoring.",
    example: "Example: Mark discharge pressure and motor current as critical for a compressor.",
  },
  {
    title: "What is duration selection?",
    body: "Duration defines how much historical data to include in the analysis window.",
    example: "Example: Last 6 months, or a custom range from Jan 1 to Jun 30.",
  },
];

export function HelpAccordion() {
  const [openIndex, setOpenIndex] = useState<number | null>(0);

  return (
    <Card>
      <CardHeader>
        <CardTitle>User Guide</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {items.map((item, index) => {
          const open = openIndex === index;
          return (
            <div key={item.title} className="rounded-lg border">
              <button
                type="button"
                className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium"
                onClick={() => setOpenIndex(open ? null : index)}
              >
                {item.title}
                <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} />
              </button>
              {open ? (
                <div className="border-t px-4 py-3 text-sm text-muted-foreground">
                  <p>{item.body}</p>
                  <p className="mt-2 rounded-md bg-muted/40 px-3 py-2 text-xs text-foreground">
                    {item.example}
                  </p>
                </div>
              ) : null}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
