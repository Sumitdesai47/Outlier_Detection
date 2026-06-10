import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useMemo } from "react";
import {
  formatNumber,
  formatTimestamp,
  markerTypeLabel,
  tableSubtitle,
} from "@/lib/resultTableFormat";
import { sortByTimestampAsc } from "@/lib/sortResults";
import type { ResultPoint, ResultTab } from "@/types/results";
import { ReasonWithEngines } from "@/components/results/ReasonWithEngines";

const helper = createColumnHelper<ResultPoint>();

const columns = [
  helper.accessor("observed_at", {
    header: "Timestamp",
    cell: (info) => formatTimestamp(info.getValue()),
  }),
  helper.accessor("tag_value", {
    header: "Tag value",
    cell: (info) => formatNumber(info.getValue()),
  }),
  helper.display({
    id: "marker_type",
    header: "Marker",
    cell: ({ row }) => markerTypeLabel(row.original),
  }),
  helper.display({
    id: "reason",
    header: "Reason",
    cell: ({ row }) => <ReasonWithEngines point={row.original} />,
  }),
];

export function ResultTable({ tab, points }: { tab: ResultTab; points: ResultPoint[] }) {
  const sortedPoints = useMemo(() => sortByTimestampAsc(points), [points]);
  const table = useReactTable({
    data: sortedPoints,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="space-y-2">
      <p className="text-sm text-muted-foreground">{tableSubtitle(tab)}</p>
      {!points.length ? (
        <p className="text-sm text-muted-foreground">No records for this tab.</p>
      ) : (
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
                    <td key={cell.id} className="px-3 py-2 align-top text-muted-foreground">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
