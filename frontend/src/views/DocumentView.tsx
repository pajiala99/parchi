import { useState } from "react";
import { api, ApiError, type DocDetail, type DocType, type Editable } from "../api";
import { DOC_TYPES, displayDate, duration, isBusy, money, percent, relativeTime, STAGE_LABELS, stageOf } from "../format";
import { usePolling } from "../hooks";

const CHECK_LABELS: Record<string, string> = {
  gstin_checksum: "GSTIN checksum",
  totals_reconcile: "Subtotal + tax = total",
  line_items_sum: "Line items add up",
  date_plausible: "Date is plausible",
};

export function DocumentView({ id }: { id: string }) {
  const [busy, setBusy] = useState(true);
  const doc = usePolling(() => api.get(id), busy ? 1200 : 20000, [id]);
  const [editing, setEditing] = useState(false);
  const [working, setWorking] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const data = doc.data;
  if (data && busy !== isBusy(data.status)) setBusy(isBusy(data.status));

  if (doc.error && !data) {
    const missing = doc.error instanceof ApiError && doc.error.status === 404;
    return (
      <div className="shell-inner">
        <p className="crumbs">
          <a href="#/">← Ledger</a>
        </p>
        <div className="card empty">
          {missing ? "This document is not in your workspace (or it was deleted)." : doc.error.message}
        </div>
      </div>
    );
  }
  if (!data) return <p className="crumbs muted">Loading…</p>;

  const act = async (name: string, action: () => Promise<unknown>, after?: () => void) => {
    setWorking(name);
    setMessage(null);
    try {
      await action();
      after?.();
      await doc.refresh();
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setWorking(null);
    }
  };

  const stage = stageOf(data.status, true);
  return (
    <>
      <p className="crumbs">
        <a href="#/">← Ledger</a>
      </p>
      <div className="detail-head">
        <div>
          <h1>{data.vendor ?? data.fileName}</h1>
          <div className="muted" style={{ marginTop: 6 }}>
            {data.fileName} · uploaded {relativeTime(data.createdAt)} ·{" "}
            <span className={`status s-${stage}`}>{STAGE_LABELS[stage]}</span>
          </div>
        </div>
        <div className="actions">
          {data.previewUrl && (
            <a className="btn" href={data.previewUrl} target="_blank" rel="noreferrer">
              Original ↗
            </a>
          )}
          {(data.status === "COMPLETED" || data.status === "FAILED") && (
            <>
              <button className="btn" disabled={!!working} onClick={() => setEditing((value) => !value)}>
                {editing ? "Cancel edit" : "Correct fields"}
              </button>
              <button
                className="btn"
                disabled={!!working}
                onClick={() =>
                  confirm("Run OCR on this document again? Your corrections will be replaced.") &&
                  act("reprocess", () => api.reprocess(id), () => setBusy(true))
                }
              >
                {working === "reprocess" ? "Queueing…" : "Reprocess"}
              </button>
            </>
          )}
          <button
            className="btn danger"
            disabled={!!working}
            onClick={() =>
              confirm("Delete this document and its file? This cannot be undone.") &&
              act("delete", () => api.remove(id), () => (window.location.hash = "#/"))
            }
          >
            Delete
          </button>
        </div>
      </div>
      {message && <div className="notice err">{message}</div>}

      <div className="detail">
        <div className="preview card">
          {data.previewUrl ? (
            data.contentType === "application/pdf" ? (
              <iframe src={data.previewUrl} title={data.fileName} />
            ) : (
              <img src={data.previewUrl} alt={`Scan of ${data.fileName}`} />
            )
          ) : (
            <div className="placeholder">The file has not reached S3 yet.</div>
          )}
        </div>

        <div>
          <Slip doc={data} editing={editing} onSave={(edits) => act("save", () => api.edit(id, edits), () => setEditing(false))} saving={working === "save"} />
          <Checks doc={data} />
          {!!data.lineItems?.length && <Items doc={data} />}
          <Timeline doc={data} />
          {!!data.otherFields?.length && (
            <div className="panel card">
              <h3>Other details Textract found</h3>
              <dl className="fields" style={{ margin: 0 }}>
                {data.otherFields.map((field, index) => (
                  <Field key={index} label={field.label}>
                    {field.value}
                  </Field>
                ))}
              </dl>
            </div>
          )}
          {data.textPreview && (
            <div className="panel card">
              <details className="raw">
                <summary>Text the OCR read ({data.engine})</summary>
                <pre>{data.textPreview}</pre>
              </details>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function Field({ label, children, big, confidence, edited }: {
  label: string;
  children: React.ReactNode;
  big?: boolean;
  confidence?: number;
  edited?: boolean;
}) {
  return (
    <>
      <dt>{label}</dt>
      <dd className={big ? "big" : undefined}>
        <span className="value">{children}</span>
        {edited ? (
          <span className="edited">corrected</span>
        ) : (
          confidence !== undefined && (
            <span className={`conf${confidence < 0.8 ? " low" : ""}`} title="OCR confidence">
              {percent(confidence)}
            </span>
          )
        )}
      </dd>
    </>
  );
}

function Slip({ doc, editing, onSave, saving }: {
  doc: DocDetail;
  editing: boolean;
  onSave: (edits: Editable) => void;
  saving: boolean;
}) {
  const conf = doc.fieldConfidence ?? {};
  const edited = new Set(doc.editedFields ?? []);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const value = (key: keyof DocDetail) => (key in draft ? draft[key] : String(doc[key] ?? ""));
  const set = (key: string) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setDraft((current) => ({ ...current, [key]: event.target.value }));

  const save = () => {
    const edits: Record<string, unknown> = {};
    for (const [key, raw] of Object.entries(draft)) {
      const trimmed = raw.trim();
      edits[key] = ["total", "tax", "subtotal"].includes(key) ? (trimmed === "" ? null : Number(trimmed.replace(/,/g, ""))) : trimmed || null;
    }
    if (Object.keys(edits).length) onSave(edits as Editable);
  };

  const stamp =
    doc.status === "FAILED" ? (
      <div className="stamp fail">UNREAD</div>
    ) : doc.status === "COMPLETED" ? (
      <div className={`stamp${doc.needsReview && !doc.reviewed ? " warn" : ""}`}>
        {doc.needsReview && !doc.reviewed ? "CHECK" : "FILED"}
        <small>{displayDate(doc.processedAt?.slice(0, 10))}</small>
      </div>
    ) : null;

  return (
    <div className="slip card">
      {stamp}
      <div className="slip-head">
        <div className="muted" style={{ fontSize: 12, letterSpacing: "0.08em", textTransform: "uppercase" }}>
          {doc.docType ? DOC_TYPES[doc.docType].label : "Document"}
          {doc.docTypeConfidence !== undefined && ` · ${evidence(doc.docTypeConfidence)}`}
        </div>
        {!!doc.docTypeSignals?.length && (
          <div className="signals" aria-label="Why this type">
            <span className="muted" style={{ fontSize: 12 }}>because:</span>
            {doc.docTypeSignals.map((signal) => (
              <span className="signal" key={signal}>
                {signal}
              </span>
            ))}
          </div>
        )}
      </div>
      {doc.status === "FAILED" && <div className="notice err">{doc.error}</div>}
      {doc.status === "QUEUED" && doc.lastError && <div className="notice warn">Retrying: {doc.lastError}</div>}

      {editing ? (
        <dl className="fields">
          <dt>Type</dt>
          <dd>
            <select value={value("docType")} onChange={set("docType")}>
              {(Object.keys(DOC_TYPES) as DocType[]).map((type) => (
                <option key={type} value={type}>
                  {DOC_TYPES[type].label}
                </option>
              ))}
            </select>
          </dd>
          {(
            [
              ["vendor", "Vendor", "text"],
              ["documentDate", "Date", "date"],
              ["total", "Total", "number"],
              ["tax", "Tax", "number"],
              ["subtotal", "Subtotal", "number"],
              ["invoiceNumber", "Reference no.", "text"],
              ["receiver", "Billed to", "text"],
            ] as const
          ).map(([key, label, type]) => (
            <Edit key={key} label={label}>
              <input type={type} step={type === "number" ? "0.01" : undefined} value={value(key)} onChange={set(key)} />
            </Edit>
          ))}
          <Edit label="Notes">
            <textarea rows={2} value={value("notes")} onChange={set("notes")} />
          </Edit>
          <dt />
          <dd>
            <button className="btn primary" disabled={saving} onClick={save}>
              {saving ? "Saving…" : "Save corrections"}
            </button>
          </dd>
        </dl>
      ) : (
        <dl className="fields">
          <Field label="Total" big confidence={conf.total} edited={edited.has("total")}>
            {money(doc.total, doc.currency)}
          </Field>
          <Field label="Vendor" confidence={conf.vendor} edited={edited.has("vendor")}>
            {doc.vendor ?? "—"}
          </Field>
          <Field label="Date" confidence={conf.documentDate} edited={edited.has("documentDate")}>
            {displayDate(doc.documentDate)}
          </Field>
          <Field label="Tax (GST)" confidence={conf.tax} edited={edited.has("tax")}>
            {money(doc.tax, doc.currency)}
          </Field>
          {doc.subtotal != null && <Field label="Subtotal">{money(doc.subtotal, doc.currency)}</Field>}
          <Field label="Reference no." confidence={conf.invoiceNumber} edited={edited.has("invoiceNumber")}>
            {doc.invoiceNumber ?? "—"}
          </Field>
          {doc.receiver && <Field label="Billed to">{doc.receiver}</Field>}
          {doc.vendorGstin && <Field label="Vendor GSTIN"><span className="num">{doc.vendorGstin}</span></Field>}
          {doc.notes && <Field label="Notes">{doc.notes}</Field>}
        </dl>
      )}
    </div>
  );
}

/** The classifier scores keyword evidence; describe its strength rather than claim a probability. */
function evidence(score: number): string {
  return score >= 0.9 ? "strong evidence" : score >= 0.6 ? "likely" : "best guess";
}

function Edit({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  );
}

function Checks({ doc }: { doc: DocDetail }) {
  if (doc.status !== "COMPLETED") return null;
  return (
    <div className="panel card">
      <h3>Checks</h3>
      {doc.checks?.length ? (
        <ul className="checks">
          {doc.checks.map((check) => (
            <li key={check.name} className={check.ok ? "ok" : "bad"}>
              <span className="icon">{check.ok ? "✓" : "!"}</span>
              <span>
                <b>{CHECK_LABELS[check.name] ?? check.name}</b>
                <br />
                <span className="muted">{check.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted" style={{ margin: 0 }}>Not enough fields were found to cross-check.</p>
      )}
      {doc.needsReview && !doc.reviewed && !!doc.reviewReasons?.length && (
        <div className="notice warn">Worth a look: {doc.reviewReasons.join("; ")}.</div>
      )}
      {doc.reviewed && <div className="notice info">You corrected this document; your values are what's filed.</div>}
    </div>
  );
}

function Items({ doc }: { doc: DocDetail }) {
  return (
    <div className="panel card">
      <h3>Line items</h3>
      <table className="items">
        <thead>
          <tr>
            <th>Item</th>
            <th className="r">Qty</th>
            <th className="r">Rate</th>
            <th className="r">Amount</th>
          </tr>
        </thead>
        <tbody>
          {doc.lineItems!.map((item, index) => (
            <tr key={index}>
              <td>{item.description}</td>
              <td className="r">{item.quantity ?? ""}</td>
              <td className="r">{item.unitPrice != null ? money(item.unitPrice, doc.currency) : ""}</td>
              <td className="r">{money(item.amount, doc.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Timeline({ doc }: { doc: DocDetail }) {
  const engine = doc.engine === "tesseract" ? "Tesseract" : doc.engine === "replay" ? "Textract (replay)" : "Textract";
  const failed = doc.status === "FAILED";
  const steps = [
    { label: "Upload URL issued", service: "API Gateway + Lambda", at: doc.createdAt, done: true },
    { label: "File stored, event published", service: "S3 → SQS", at: doc.uploadedAt, done: !!doc.uploadedAt },
    {
      label: "Picked up by the worker",
      service: "Lambda",
      at: doc.processingStartedAt,
      note: doc.timings?.queueMs != null ? `waited ${duration(doc.timings.queueMs)} in the queue` : undefined,
      done: !!doc.processingStartedAt,
    },
    {
      label: failed ? "Could not be read" : "Read",
      service: engine,
      note: doc.timings?.extractMs != null ? `took ${duration(doc.timings.extractMs)}` : undefined,
      done: doc.status === "COMPLETED",
      bad: failed,
    },
    {
      label: "Checked and filed",
      service: "DynamoDB",
      at: doc.processedAt,
      note: doc.timings?.totalMs != null ? `${duration(doc.timings.totalMs)} after the upload` : undefined,
      done: doc.status === "COMPLETED",
    },
    {
      label: doc.notified ? "Confirmation emailed" : "Email",
      service: "SNS",
      at: doc.notifiedAt,
      note: doc.notified ? undefined : doc.status === "COMPLETED" ? "no email address set (Settings)" : undefined,
      done: !!doc.notified,
    },
  ];
  const firstOpen = steps.findIndex((step) => !step.done);
  return (
    <div className="panel card">
      <h3>Pipeline</h3>
      <ol className="timeline">
        {steps.map((step, index) => (
          <li key={step.label} className={step.bad ? "bad" : step.done ? "done" : index === firstOpen && isBusy(doc.status) ? "active" : ""}>
            <span className="dot" />
            {step.label}
            <span className="service">{step.service}</span>
            {(step.at || step.note) && (
              <span className="when">
                {step.at && new Date(step.at).toLocaleTimeString()}
                {step.at && step.note && " · "}
                {step.note}
              </span>
            )}
          </li>
        ))}
      </ol>
      {doc.attempts && doc.attempts > 1 ? <p className="muted" style={{ margin: 0, fontSize: 13 }}>Processed {doc.attempts} times.</p> : null}
    </div>
  );
}
