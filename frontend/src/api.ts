// Typed client for the Parchi API (backend/src/parchi/handlers). Every call carries the
// browser's workspace key; files never pass through the API: they go straight to S3.

export type Status = "AWAITING_UPLOAD" | "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED";
export type DocType = "FEE_RECEIPT" | "MEDICAL_BILL" | "INVOICE" | "OTHER";

export interface Timings {
  queueMs?: number | null;
  extractMs?: number | null;
  totalMs?: number | null;
}

export interface DocSummary {
  documentId: string;
  fileName: string;
  contentType: string;
  sizeBytes?: number;
  declaredSize?: number;
  status: Status;
  docType?: DocType;
  vendor?: string;
  documentDate?: string;
  total?: number;
  currency?: string;
  confidence?: number;
  needsReview?: boolean;
  reviewed?: boolean;
  createdAt: string;
  uploadedAt?: string;
  processedAt?: string;
  error?: string;
  engine?: string;
  timings?: Timings;
  notified?: boolean;
}

export interface LineItem {
  description: string;
  quantity?: number;
  unitPrice?: number;
  amount?: number;
}

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
}

export interface DocDetail extends DocSummary {
  invoiceNumber?: string;
  receiver?: string;
  subtotal?: number;
  tax?: number;
  vendorGstin?: string;
  lineItems?: LineItem[];
  otherFields?: { label: string; value: string }[];
  fieldConfidence?: Record<string, number>;
  checks?: Check[];
  reviewReasons?: string[];
  docTypeSignals?: string[];
  docTypeConfidence?: number;
  textPreview?: string;
  previewUrl: string | null;
  notes?: string;
  editedFields?: string[];
  processingStartedAt?: string;
  notifiedAt?: string;
  failedAt?: string;
  queuedAt?: string;
  attempts?: number;
  lastError?: string;
  pages?: number;
}

export interface Stats {
  documents: number;
  byStatus: Partial<Record<Status, number>>;
  byType: { docType: DocType; count: number; total: number }[];
  byMonth: { month: string; count: number; total: number }[];
  topVendors: { vendor: string; count: number; total: number }[];
  currency: string;
  totalSpend: number;
  spendThisMonth: number;
  needsReview: number;
  latency: { endToEndMsP50: number | null; endToEndMsP90: number | null; extractMsP50: number | null };
  averageConfidence: number | null;
  usage: { uploadsToday: number; dailyLimit: number };
}

export interface Health {
  ok: boolean;
  version: string;
  stage: string;
  region: string;
  engine: string;
  limits: { maxUploadBytes: number; dailyUploadsPerWorkspace: number };
}

export interface Subscription {
  email: string | null;
  status: "none" | "pending" | "confirmed";
}

export interface OutboxMail {
  id: string;
  documentId?: string;
  subject: string;
  body: string;
  sentAt: string;
  to?: string | null;
}

export type Editable = Partial<
  Pick<
    DocDetail,
    "vendor" | "documentDate" | "total" | "tax" | "subtotal" | "invoiceNumber" | "receiver" | "notes" | "docType"
  >
>;

// ------------------------------------------------------------ workspace key

const KEY_STORAGE = "parchi.workspaceKey";
const KEY_PATTERN = /^[A-Za-z0-9_-]{32,128}$/;
let memoryKey: string | null = null;

export function newKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  let binary = "";
  bytes.forEach((b) => (binary += String.fromCharCode(b)));
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function isValidKey(key: string | null | undefined): key is string {
  return !!key && KEY_PATTERN.test(key);
}

export function workspaceKey(): string {
  let key: string | null = memoryKey;
  try {
    key = localStorage.getItem(KEY_STORAGE) ?? key;
  } catch {
    /* storage unavailable: keep the key in memory for this page */
  }
  if (!isValidKey(key)) {
    key = newKey();
    setWorkspaceKey(key);
  }
  return key;
}

export function setWorkspaceKey(key: string): void {
  memoryKey = key;
  try {
    localStorage.setItem(KEY_STORAGE, key);
  } catch {
    /* ignore */
  }
}

// ------------------------------------------------------------------ calls

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "content-type": "application/json", "x-parchi-key": workspaceKey(), ...init.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, body?.error?.message ?? body?.message ?? response.statusText, body?.error?.code);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const api = {
  health: () => call<Health>("/health"),
  list: () => call<{ items: DocSummary[]; nextCursor: string | null }>("/documents?limit=100"),
  get: (id: string) => call<DocDetail>(`/documents/${encodeURIComponent(id)}`),
  edit: (id: string, edits: Editable) =>
    call<DocDetail>(`/documents/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(edits) }),
  remove: (id: string) => call<void>(`/documents/${encodeURIComponent(id)}`, { method: "DELETE" }),
  reprocess: (id: string) => call<DocDetail>(`/documents/${encodeURIComponent(id)}/reprocess`, { method: "POST" }),
  stats: () => call<Stats>("/stats"),
  subscription: () => call<Subscription>("/subscription"),
  subscribe: (email: string) => call<Subscription>("/subscription", { method: "PUT", body: JSON.stringify({ email }) }),
  unsubscribe: () => call<void>("/subscription", { method: "DELETE" }),
  outbox: () => call<{ items: OutboxMail[] }>("/dev/outbox"),
};

// ---------------------------------------------------------------- uploads

const TYPES: Record<string, string> = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", pdf: "application/pdf" };
export const ACCEPTED_TYPES = ["image/jpeg", "image/png", "application/pdf"];

export function contentTypeOf(file: { name: string; type: string }): string | null {
  const declared = file.type === "image/jpg" ? "image/jpeg" : file.type;
  if (ACCEPTED_TYPES.includes(declared)) return declared;
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  return TYPES[extension] ?? null;
}

/** Ask the API for a presigned POST, then send the file straight to S3 with progress events. */
export async function uploadFile(file: File, onProgress: (fraction: number) => void): Promise<string> {
  const contentType = contentTypeOf(file);
  if (!contentType) throw new ApiError(415, "Upload a JPEG or PNG photo, or a PDF.");
  const { documentId, upload } = await call<{
    documentId: string;
    upload: { url: string; fields: Record<string, string> };
  }>("/uploads", { method: "POST", body: JSON.stringify({ fileName: file.name, contentType, size: file.size }) });
  const form = new FormData();
  Object.entries(upload.fields).forEach(([name, value]) => form.append(name, value));
  form.append("file", file); // S3 requires the file to be the last field
  await new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", upload.url);
    xhr.upload.onprogress = (event) => event.lengthComputable && onProgress(event.loaded / event.total);
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new ApiError(xhr.status, "S3 rejected the upload."));
    xhr.onerror = () => reject(new ApiError(0, "Network error while uploading to S3."));
    xhr.send(form);
  });
  onProgress(1);
  return documentId;
}
