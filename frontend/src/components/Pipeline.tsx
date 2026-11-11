import type { DocSummary } from "../api";
import { stageOf } from "../format";

const HOPS = [
  { name: "API Gateway", note: "upload URL" },
  { name: "S3", note: "file stored" },
  { name: "SQS", note: "event queued" },
  { name: "Lambda", note: "worker" },
  { name: "OCR", note: "Textract" },
  { name: "DynamoDB", note: "filed" },
  { name: "SNS", note: "email" },
];

/** How far along the pipeline a document is: index of the hop currently working on it. */
export function hopIndex(doc: DocSummary, uploadedHere: boolean): number {
  switch (stageOf(doc.status, uploadedHere)) {
    case "waiting":
      return 1;
    case "queued":
      return 2;
    case "reading":
      return 4;
    case "filed":
      return doc.notified ? 7 : 6;
    default:
      return -1;
  }
}

export function PipelineStrip({ doc, uploadedHere, engine }: { doc?: DocSummary; uploadedHere: boolean; engine: string }) {
  const at = doc ? hopIndex(doc, uploadedHere) : -2;
  return (
    <div className="pipeline card" aria-label="Pipeline status of the latest document">
      <span className="pipeline-title">Live pipeline</span>
      {HOPS.map((hop, index) => {
        const finished = doc?.status === "COMPLETED";
        const state = at === -1 ? "" : index < at ? "done" : index === at && !finished ? "active" : "";
        const label = hop.name === "OCR" ? (engine === "textract" ? "Textract" : engine === "tesseract" ? "Tesseract" : "OCR") : hop.name;
        return (
          <span key={hop.name} style={{ display: "contents" }}>
            {index > 0 && <span className="hop-arrow">→</span>}
            <span className={`hop ${state}`} title={hop.note}>
              {state === "done" ? "✓ " : ""}
              {label}
            </span>
          </span>
        );
      })}
      <span className="pipeline-doc">
        {doc ? (at === -1 ? `✗ ${doc.fileName} failed` : doc.fileName) : "upload something to watch it travel"}
      </span>
    </div>
  );
}
