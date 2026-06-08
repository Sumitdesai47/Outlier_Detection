import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useMemo } from "react";
import { sortByTimestampAsc } from "@/lib/sortResults";
import type { ResultPoint, ResultTab } from "@/types/results";
import { StatusBadge } from "@/components/results/StatusBadge";

const helper = createColumnHelper<ResultPoint>();

function columnsForTab(tab: ResultTab) {
  const base = [
    helper.accessor("observed_at", { header: "Timestamp" }),
    helper.accessor("tag_name", { header: "Tag name" }),
    helper.accessor("tag_value", { header: "Tag value" }),
    helper.accessor("status", {
      header: "Status",
      cell: (info) => <StatusBadge status={info.getValue()} />,
    }),
  ];

  if (tab === "outlier") {
    return [
      ...base,
      helper.accessor("outlier_score", { header: "Outlier score" }),
      helper.accessor("lower_limit", { header: "Lower limit" }),
      helper.accessor("upper_limit", { header: "Upper limit" }),
      helper.accessor("reason", { header: "Reason" }),
    ];
  }
  if (tab === "process") {
    return [
      ...base,
      helper.accessor("related_tags", {
        header: "Related tags",
        cell: (info) => (info.getValue() || []).join(", "),
      }),
      helper.accessor("process_issue_score", { header: "Process issue score" }),
      helper.accessor("reason", { header: "Reason" }),
      helper.accessor("interpretation", { header: "Suggested interpretation" }),
    ];
  }
  return [
    ...base,
    helper.accessor("outlier_score", { header: "Outlier score" }),
    helper.accessor("process_issue_score", { header: "Process issue score" }),
    helper.accessor("related_tags", {
      header: "Related tags",
      cell: (info) => (info.getValue() || []).join(", "),
    }),
    helper.accessor("reason", { header: "Reason" }),
    helper.accessor("suggested_action", { header: "Suggested action" }),
  ];
}

export function ResultTable({ tab, points }: { tab: ResultTab; points: ResultPoint[] }) {
  const sortedPoints = useMemo(() => sortByTimestampAsc(points), [points]);
  const columns = useMemo(() => columnsForTab(tab), [tab]);
  const table = useReactTable({
    data: sortedPoints,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  if (!points.length) {
    return <p className="text-sm text-muted-foreground">No records for this tab.</p>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="min-w-full text-left text-sm">
        <thead className="bg-muted/50">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((header) => (
                <th key={header.id} className="px-3 py-2 font-semibold">
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="border-t">
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-3 py-2 text-muted-foreground">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
