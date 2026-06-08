import Papa from "papaparse";
import * as XLSX from "xlsx";
import { detectColumns } from "./columnDetection";
import type { ParsedDataset } from "@/types";

function normalizeRows(rawRows: Record<string, unknown>[]): Record<string, unknown>[] {
  return rawRows
    .map((row) => {
      const normalized: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(row)) {
        const cleanKey = String(key).trim();
        if (!cleanKey) continue;
        normalized[cleanKey] = value;
      }
      return normalized;
    })
    .filter((row) => Object.keys(row).length > 0);
}

function buildParsedDataset(
  fileName: string,
  rows: Record<string, unknown>[],
): ParsedDataset {
  const columns = rows.length > 0 ? Object.keys(rows[0]) : [];
  const detection = detectColumns(rows, columns);

  return {
    fileName,
    rowCount: rows.length,
    columnCount: columns.length,
    columns,
    previewRows: rows.slice(0, 5),
    ...detection,
  };
}

function parseCsv(file: File): Promise<ParsedDataset> {
  return new Promise((resolve, reject) => {
    Papa.parse<Record<string, unknown>>(file, {
      header: true,
      skipEmptyLines: true,
      dynamicTyping: false,
      complete: (results) => {
        const rows = normalizeRows(results.data);
        resolve(buildParsedDataset(file.name, rows));
      },
      error: (error) => reject(error),
    });
  });
}

function parseExcel(file: File): Promise<ParsedDataset> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const data = event.target?.result;
        const workbook = XLSX.read(data, { type: "array" });
        const sheetName = workbook.SheetNames[0];
        const sheet = workbook.Sheets[sheetName];
        const rawRows = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, {
          defval: "",
        });
        const rows = normalizeRows(rawRows);
        resolve(buildParsedDataset(file.name, rows));
      } catch (error) {
        reject(error);
      }
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

export async function parseDatasetFile(file: File): Promise<ParsedDataset> {
  const ext = file.name.split(".").pop()?.toLowerCase();
  if (ext === "csv") return parseCsv(file);
  if (ext === "xlsx" || ext === "xls") return parseExcel(file);
  throw new Error("Unsupported file type. Please upload .xlsx, .xls, or .csv.");
}
