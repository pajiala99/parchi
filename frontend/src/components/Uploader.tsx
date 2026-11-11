import { useRef, useState } from "react";
import { ACCEPTED_TYPES, contentTypeOf } from "../api";
import { fileSize } from "../format";

const SAMPLES = [
  { file: "pharmacy-bill-photo.jpg", label: "Pharmacy bill (photo)" },
  { file: "fee-receipt.png", label: "Fee receipt" },
  { file: "tax-invoice.png", label: "Tax invoice" },
  { file: "clinic-bill.pdf", label: "Clinic bill (PDF)" },
  { file: "canteen-receipt.png", label: "Canteen receipt" },
];

interface Props {
  maxBytes: number;
  onFiles: (files: File[]) => void;
  onReject: (message: string) => void;
}

export function Uploader({ maxBytes, onFiles, onReject }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [loadingSample, setLoadingSample] = useState<string | null>(null);

  const accept = (list: FileList | File[]) => {
    const good: File[] = [];
    for (const file of Array.from(list)) {
      if (!contentTypeOf(file)) onReject(`${file.name}: only JPEG, PNG and PDF files can be read.`);
      else if (file.size > maxBytes) onReject(`${file.name} is ${fileSize(file.size)}; the limit is ${fileSize(maxBytes)}.`);
      else if (file.size === 0) onReject(`${file.name} is empty.`);
      else good.push(file);
    }
    if (good.length) onFiles(good);
  };

  const trySample = async (name: string) => {
    setLoadingSample(name);
    try {
      const response = await fetch(`/samples/${name}`);
      if (!response.ok) throw new Error(`Could not load the sample ${name}.`);
      const blob = await response.blob();
      accept([new File([blob], name, { type: contentTypeOf({ name, type: blob.type }) ?? blob.type })]);
    } catch (error) {
      onReject((error as Error).message);
    } finally {
      setLoadingSample(null);
    }
  };

  return (
    <div>
      <div
        className={`drop card${over ? " over" : ""}`}
        role="button"
        tabIndex={0}
        aria-label="Upload documents"
        onClick={() => input.current?.click()}
        onKeyDown={(event) => (event.key === "Enter" || event.key === " ") && input.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          accept(event.dataTransfer.files);
        }}
      >
        <div className="drop-icon" aria-hidden>
          ⇪
        </div>
        <div className="drop-title">Drop a bill, receipt or invoice</div>
        <div className="muted">JPEG, PNG or single-page PDF up to {fileSize(maxBytes)} · it goes straight to S3</div>
        <input
          ref={input}
          type="file"
          hidden
          multiple
          accept={ACCEPTED_TYPES.join(",")}
          onChange={(event) => {
            if (event.target.files) accept(event.target.files);
            event.target.value = "";
          }}
        />
      </div>
      <div className="samples">
        <span className="muted">No bill handy? Try a sample:</span>
        {SAMPLES.map((sample) => (
          <button
            key={sample.file}
            className="chip-button"
            disabled={loadingSample !== null}
            onClick={() => trySample(sample.file)}
          >
            {loadingSample === sample.file ? "Loading…" : sample.label}
          </button>
        ))}
      </div>
    </div>
  );
}
