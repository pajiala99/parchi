import type { DocType, Status } from "./api";

export const DOC_TYPES: Record<DocType, { label: string; short: string }> = {
  FEE_RECEIPT: { label: "Fee receipt", short: "Fees" },
  MEDICAL_BILL: { label: "Medical bill", short: "Medical" },
  INVOICE: { label: "Invoice", short: "Invoices" },
  OTHER: { label: "Other receipt", short: "Other" },
};

/** Indian digit grouping: 123456.5 -> "1,23,456.50". */
export function groupIndian(value: number): string {
  const [whole, fraction] = Math.abs(value).toFixed(2).split(".");
  const last3 = whole.slice(-3);
  const rest = whole.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${value < 0 ? "-" : ""}${rest ? `${rest},${last3}` : last3}.${fraction}`;
}

export function money(value: number | null | undefined, currency = "INR"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (currency === "INR") return `₹${groupIndian(value)}`;
  const symbol = { USD: "$", EUR: "€", GBP: "£" }[currency];
  return symbol ? `${symbol}${value.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : `${currency} ${value}`;
}

export function shortMoney(value: number, currency = "INR"): string {
  if (currency !== "INR") return money(value, currency);
  if (value >= 1e7) return `₹${(value / 1e7).toFixed(1)}Cr`;
  if (value >= 1e5) return `₹${(value / 1e5).toFixed(1)}L`;
  if (value >= 1e3) return `₹${(value / 1e3).toFixed(1)}k`;
  return `₹${Math.round(value)}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-11-08" -> "8 Nov 2026" (dates on bills have no time zone; don't shift them). */
export function displayDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return iso;
  return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}`;
}

export function monthLabel(month: string): string {
  const [year, number] = month.split("-");
  return `${MONTHS[Number(number) - 1]} ${year.slice(2)}`;
}

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

export function percent(fraction: number | null | undefined): string {
  return fraction === null || fraction === undefined ? "—" : `${Math.round(fraction * 100)}%`;
}

export function fileSize(bytes: number | null | undefined): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export type Stage = "waiting" | "queued" | "reading" | "filed" | "failed";

/** What a person should see, combining the server status with what this browser knows. */
export function stageOf(status: Status, uploadedHere = false): Stage {
  switch (status) {
    case "AWAITING_UPLOAD":
      return uploadedHere ? "queued" : "waiting";
    case "QUEUED":
      return "queued";
    case "PROCESSING":
      return "reading";
    case "COMPLETED":
      return "filed";
    default:
      return "failed";
  }
}

export const STAGE_LABELS: Record<Stage, string> = {
  waiting: "Waiting for upload",
  queued: "In queue",
  reading: "Reading",
  filed: "Filed",
  failed: "Failed",
};

export const isBusy = (status: Status) => status === "AWAITING_UPLOAD" || status === "QUEUED" || status === "PROCESSING";
