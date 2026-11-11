import { useEffect, useMemo, useRef, useState } from "react";
import { api, type DocType, type Health, type Stats, uploadFile } from "../api";
import { PipelineStrip } from "../components/Pipeline";
import { Uploader } from "../components/Uploader";
import {
  DOC_TYPES,
  displayDate,
  duration,
  isBusy,
  money,
  monthLabel,
  relativeTime,
  shortMoney,
  STAGE_LABELS,
  stageOf,
} from "../format";
import { usePolling } from "../hooks";

interface LocalUpload {
  id: string;
  name: string;
  progress: number;
  documentId?: string;
  error?: string;
}

type Filter = "ALL" | DocType | "REVIEW";
const TYPE_COLORS: Record<DocType, string> = {
  FEE_RECEIPT: "var(--pen)",
  MEDICAL_BILL: "var(--err)",
  INVOICE: "var(--ok)",
  OTHER: "var(--muted)",
};
const TYPE_BADGE: Record<DocType, string> = { FEE_RECEIPT: "FR", MEDICAL_BILL: "Rx", INVOICE: "IN", OTHER: "··" };

// Survives navigating to a document and back: which documents this browser uploaded.
const uploadedHere = new Set<string>();

export function Dashboard({ health }: { health: Health | null }) {
  const [uploads, setUploads] = useState<LocalUpload[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("ALL");
  const [query, setQuery] = useState("");
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const seen = useRef<Map<string, string>>(new Map());

  const [busy, setBusy] = useState(false);
  const list = usePolling(api.list, busy ? 1500 : 15000);
  const stats = usePolling(api.stats, busy ? 3000 : 30000);
  const documents = useMemo(() => list.data?.items ?? [], [list.data]);

  useEffect(() => {
    setBusy(uploads.some((u) => !u.error) || documents.some((d) => isBusy(d.status)));
  }, [uploads, documents]);

  useEffect(() => {
    // Stamp rows that finish while you watch, and refresh the totals.
    const justFiled: string[] = [];
    for (const doc of documents) {
      const before = seen.current.get(doc.documentId);
      if (before && before !== "COMPLETED" && doc.status === "COMPLETED") justFiled.push(doc.documentId);
      seen.current.set(doc.documentId, doc.status);
    }
    if (justFiled.length) {
      setFresh((current) => new Set([...current, ...justFiled]));
      stats.refresh();
    }
    // A local row can go once the server lists the document.
    setUploads((current) =>
      current.filter((u) => u.error || !u.documentId || !documents.some((d) => d.documentId === u.documentId)),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documents]);

  const start = (files: File[]) => {
    setNotice(null);
    for (const file of files) {
      const id = `${Date.now()}-${Math.random()}`;
      setUploads((current) => [{ id, name: file.name, progress: 0 }, ...current]);
      const update = (patch: Partial<LocalUpload>) =>
        setUploads((current) => current.map((u) => (u.id === id ? { ...u, ...patch } : u)));
      uploadFile(file, (progress) => update({ progress }))
        .then((documentId) => {
          uploadedHere.add(documentId);
          update({ documentId, progress: 1 });
          list.refresh();
          stats.refresh();
        })
        .catch((error: Error) => update({ error: error.message }));
    }
  };

  const counts = useMemo(() => {
    const result: Record<string, number> = { ALL: documents.length, REVIEW: 0 };
    for (const doc of documents) {
      if (doc.docType) result[doc.docType] = (result[doc.docType] ?? 0) + 1;
      if (doc.needsReview && doc.status === "COMPLETED") result.REVIEW += 1;
    }
    return result;
  }, [documents]);

  const visible = documents.filter((doc) => {
    if (filter === "REVIEW" && !(doc.needsReview && doc.status === "COMPLETED")) return false;
    if (filter !== "ALL" && filter !== "REVIEW" && doc.docType !== filter) return false;
    return `${doc.fileName} ${doc.vendor ?? ""}`.toLowerCase().includes(query.trim().toLowerCase());
  });

  const latest = documents[0];
  const maxBytes = health?.limits.maxUploadBytes ?? 10 * 1024 * 1024;

  return (
    <>
      <section className="hero">
        <div>
          <div className="muted" style={{ fontSize: 13, letterSpacing: "0.08em", textTransform: "uppercase" }}>
            Serverless document pipeline
          </div>
          <h1>
            Every <em>parchi</em>, read and filed, with no server in sight.
          </h1>
          <p className="lede">
            Upload a fee receipt, medical bill or invoice. S3 fires an event, a Lambda function asks Textract to read
            it, the fields are checked and filed in DynamoDB, and SNS emails you a confirmation.
          </p>
          <Uploader maxBytes={maxBytes} onFiles={start} onReject={setNotice} />
          {notice && <div className="notice err">{notice}</div>}
        </div>
        <StatsPanel stats={stats.data} />
      </section>

      <PipelineStrip
        doc={latest}
        uploadedHere={latest ? uploadedHere.has(latest.documentId) : false}
        engine={health?.engine ?? "textract"}
      />

      <div className="ledger-head">
        <h2>Ledger</h2>
        <div className="tabs" role="group" aria-label="Filter by type">
          {(["ALL", "FEE_RECEIPT", "MEDICAL_BILL", "INVOICE", "OTHER", "REVIEW"] as Filter[]).map((key) => (
            <button key={key} className="tab" aria-pressed={filter === key} onClick={() => setFilter(key)}>
              {key === "ALL" ? "All" : key === "REVIEW" ? "Needs review" : DOC_TYPES[key].short}
              <span className="count">{counts[key] ?? 0}</span>
            </button>
          ))}
        </div>
        <input
          className="search"
          type="search"
          placeholder="Search vendor or file"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Search documents"
        />
      </div>

      <div className="ledger card">
        <div className="row header">
          <span />
          <span>Document</span>
          <span>Vendor</span>
          <span>Date</span>
          <span style={{ textAlign: "right" }}>Amount</span>
          <span>Status</span>
        </div>
        {uploads.map((upload) => (
          <div className="row" key={upload.id}>
            <span className="type-dot t-none">↑</span>
            <span className="file">
              <strong>{upload.name}</strong>
              {upload.error ? (
                <span style={{ color: "var(--err)" }}>{upload.error}</span>
              ) : (
                <span className="progress" aria-label="Upload progress">
                  <span style={{ width: `${Math.round(upload.progress * 100)}%` }} />
                </span>
              )}
            </span>
            <span className="vendor muted">—</span>
            <span className="date muted">—</span>
            <span className="amount muted">—</span>
            <span>
              <span className={`status ${upload.error ? "s-failed" : "s-queued"}`}>
                {upload.error ? "Not uploaded" : upload.documentId ? "In queue" : "Uploading to S3"}
              </span>
            </span>
          </div>
        ))}
        {visible.map((doc) => {
          const stage = stageOf(doc.status, uploadedHere.has(doc.documentId));
          return (
            <a className={`row${fresh.has(doc.documentId) ? " fresh" : ""}`} href={`#/doc/${doc.documentId}`} key={doc.documentId}>
              <span className={`type-dot t-${doc.docType ?? "none"}`} title={doc.docType ? DOC_TYPES[doc.docType].label : ""}>
                {doc.docType ? TYPE_BADGE[doc.docType] : "··"}
              </span>
              <span className="file">
                <strong>{doc.fileName}</strong>
                <span>
                  {doc.docType ? DOC_TYPES[doc.docType].label : "Not read yet"} · {relativeTime(doc.createdAt)}
                  {doc.timings?.totalMs != null && ` · filed in ${duration(doc.timings.totalMs)}`}
                </span>
              </span>
              <span className="vendor">{doc.vendor ?? <span className="muted">—</span>}</span>
              <span className="date num">{displayDate(doc.documentDate)}</span>
              <span className="amount">{money(doc.total, doc.currency)}</span>
              <span>
                <span className={`status s-${stage}`}>{STAGE_LABELS[stage]}</span>
                {doc.needsReview && doc.status === "COMPLETED" && <span className="review">check</span>}
              </span>
            </a>
          );
        })}
        {!uploads.length && !visible.length && (
          <div className="empty">
            {list.error
              ? `Could not reach the API (${list.error.message}). Is the backend running?`
              : documents.length
                ? "Nothing matches that filter."
                : "Your ledger is empty. Drop a bill above, or try a sample."}
          </div>
        )}
      </div>

      {stats.data && stats.data.byStatus.COMPLETED ? <Charts stats={stats.data} /> : null}
    </>
  );
}

function StatsPanel({ stats }: { stats: Stats | null }) {
  const used = stats?.usage.uploadsToday ?? 0;
  const limit = stats?.usage.dailyLimit ?? 0;
  return (
    <div className="stats">
      <div className="stat card">
        <div className="stat-label">Filed</div>
        <div className="stat-value num">{stats?.byStatus.COMPLETED ?? 0}</div>
        <div className="stat-note">{stats ? `${stats.documents} uploaded in total` : "—"}</div>
      </div>
      <div className="stat card">
        <div className="stat-label">Spent this month</div>
        <div className="stat-value num">{stats ? money(stats.spendThisMonth, stats.currency) : "—"}</div>
        <div className="stat-note">{stats ? `${money(stats.totalSpend, stats.currency)} all time` : ""}</div>
      </div>
      <div className="stat card">
        <div className="stat-label">Needs a look</div>
        <div className="stat-value num" style={{ color: stats?.needsReview ? "var(--warn)" : undefined }}>
          {stats?.needsReview ?? 0}
        </div>
        <div className="stat-note">a check failed or a field is missing</div>
      </div>
      <div className="stat card">
        <div className="stat-label">Upload → filed</div>
        <div className="stat-value num">{duration(stats?.latency.endToEndMsP50)}</div>
        <div className="stat-note">median · OCR {duration(stats?.latency.extractMsP50)}</div>
      </div>
      <div className="stat card" style={{ gridColumn: "1 / -1" }}>
        <div className="stat-label">Today's uploads</div>
        <div className="stat-note" style={{ marginTop: 4 }}>
          <span className="num">{used}</span> of <span className="num">{limit}</span> used · a daily cap keeps OCR
          inside the free tier
        </div>
        <div className="meter">
          <span style={{ width: `${limit ? Math.min(100, (used / limit) * 100) : 0}%` }} />
        </div>
      </div>
    </div>
  );
}

function Charts({ stats }: { stats: Stats }) {
  const topType = Math.max(1, ...stats.byType.map((t) => t.total));
  const topMonth = Math.max(1, ...stats.byMonth.map((m) => m.total));
  const thisMonth = stats.byMonth[stats.byMonth.length - 1]?.month;
  return (
    <div className="charts">
      <div className="chart card">
        <h3>Where the money went</h3>
        {stats.byType.map((type) => (
          <div className="bar-row" key={type.docType}>
            <span>{DOC_TYPES[type.docType].label}</span>
            <span className="bar-track">
              <span style={{ width: `${(type.total / topType) * 100}%`, background: TYPE_COLORS[type.docType] }} />
            </span>
            <span className="num" style={{ textAlign: "right" }}>
              {shortMoney(type.total, stats.currency)}
            </span>
          </div>
        ))}
        {stats.topVendors.length > 0 && (
          <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>
            Top vendor: {stats.topVendors[0].vendor} ({money(stats.topVendors[0].total, stats.currency)})
          </p>
        )}
      </div>
      <div className="chart card">
        <h3>By bill date, last six months</h3>
        <div className="months">
          {stats.byMonth.map((month) => (
            <div className={`month${month.month === thisMonth ? " current" : ""}`} key={month.month}>
              <span className="num" style={{ color: "var(--ink-2)" }}>
                {month.total ? shortMoney(month.total, stats.currency) : ""}
              </span>
              <span className="col" style={{ height: `${(month.total / topMonth) * 80}%` }} />
              <span>{monthLabel(month.month)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
