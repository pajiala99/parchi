// The architecture explained on the page itself, for demos and the viva.

const NODES = [
  { id: "browser", x: 20, y: 185, title: "Browser", sub: "React dashboard" },
  { id: "cdn", x: 215, y: 50, title: "CloudFront", sub: "S3 site + /api/*" },
  { id: "api", x: 410, y: 50, title: "API Gateway", sub: "HTTP API, throttled" },
  { id: "fn", x: 605, y: 50, title: "Lambda × 3", sub: "uploads · docs · email" },
  { id: "db", x: 820, y: 185, title: "DynamoDB", sub: "single table, on-demand" },
  { id: "s3", x: 215, y: 300, title: "S3 uploads", sub: "private, SSE, CORS" },
  { id: "sqs", x: 410, y: 300, title: "SQS", sub: "ingest queue + DLQ" },
  { id: "worker", x: 605, y: 300, title: "Lambda worker", sub: "processor, ≤5 at once" },
  { id: "ocr", x: 605, y: 410, title: "Textract", sub: "AnalyzeExpense" },
  { id: "sns", x: 820, y: 360, title: "SNS", sub: "email, filtered per owner" },
] as const;

const W = 160;
const H = 58;

type Edge = { d: string; async?: boolean; step?: number; sx?: number; sy?: number; label?: string; lx?: number; ly?: number };

const EDGES: Edge[] = [
  { d: `M${20 + W},${200} C 190,200 190,79 215,79`, step: 1, sx: 178, sy: 140, label: "POST /api/uploads", lx: 120, ly: 120 },
  { d: `M${215 + W},79 L410,79` },
  { d: `M${410 + W},79 L605,79` },
  { d: `M${605 + W},79 C 800,79 840,120 880,185`, label: "record: waiting", lx: 790, ly: 115 },
  { d: `M${20 + W},${225} C 190,225 190,329 215,329`, step: 2, sx: 178, sy: 270, label: "file, presigned POST", lx: 60, ly: 290 },
  { d: `M${215 + W},329 L410,329`, async: true, step: 3, sx: 392, sy: 312, label: "ObjectCreated", lx: 395, ly: 360 },
  { d: `M${410 + W},329 L605,329`, async: true, step: 4, sx: 587, sy: 312, label: "batches of 5", lx: 585, ly: 360 },
  { d: `M685,${300 + H} L685,410`, step: 5, sx: 697, sy: 388 },
  { d: `M${605 + W},315 C 800,315 860,280 890,${185 + H}`, step: 6, sx: 812, sy: 300, label: "fields, checks", lx: 800, ly: 280 },
  { d: `M${605 + W},345 C 790,345 800,389 820,389`, async: true, step: 7, sx: 798, sy: 352 },
];

const COST = [
  ["Textract AnalyzeExpense", "1,000 pages × $0.01", "$10.00"],
  ["Lambda (arm64)", "~1,000 worker runs × 3 s × 512 MB + API calls", "$0.03"],
  ["API Gateway HTTP API", "~12,000 requests (includes dashboard polling)", "$0.01"],
  ["S3", "1,000 uploads, ~0.3 GB stored", "$0.01"],
  ["DynamoDB on-demand", "~6 writes and ~10 reads per document", "$0.01"],
  ["SQS + SNS", "~3,000 queue requests, 1,000 emails", "$0.02"],
  ["CloudFront", "a few MB of dashboard and API traffic", "< $0.01"],
];

export function HowItWorks() {
  return (
    <div className="how">
      <h1>How Parchi works</h1>
      <p className="lede">
        An event-driven pipeline built only from managed AWS services. Nothing is provisioned to wait for traffic:
        each piece runs when an event arrives and costs nothing while idle. Defined as code in{" "}
        <code>template.yaml</code> (AWS SAM) and deployed with one command.
      </p>

      <div className="arch card">
        <svg viewBox="0 0 1000 480" role="img" aria-label="Architecture diagram of the Parchi pipeline">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" style={{ fill: "var(--ink-2)" }} />
            </marker>
            <marker id="arrow-async" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" style={{ fill: "var(--stamp)" }} />
            </marker>
          </defs>
          {EDGES.map((edge, index) => (
            <g key={index}>
              <path className={`edge${edge.async ? " async" : ""}`} d={edge.d} markerEnd={`url(#${edge.async ? "arrow-async" : "arrow"})`} />
              {edge.label && (
                <text className="label" x={edge.lx} y={edge.ly}>
                  {edge.label}
                </text>
              )}
              {edge.step && (
                <g>
                  <circle className="step" cx={edge.sx} cy={edge.sy} r="10" />
                  <text className="step-text" x={edge.sx} y={(edge.sy ?? 0) + 4} textAnchor="middle">
                    {edge.step}
                  </text>
                </g>
              )}
            </g>
          ))}
          <text className="label" x="905" y="448" textAnchor="middle">
            ✉ to you
          </text>
          <path className="edge async" d="M900,418 L900,432" markerEnd="url(#arrow-async)" />
          {NODES.map((node) => (
            <g key={node.id} className={`node${node.id === "browser" ? "" : " aws"}`}>
              <rect x={node.x} y={node.y} width={W} height={H} rx="10" />
              <text x={node.x + 12} y={node.y + 24}>
                {node.title}
              </text>
              <text className="sub" x={node.x + 12} y={node.y + 43}>
                {node.sub}
              </text>
            </g>
          ))}
        </svg>
        <p className="muted" style={{ fontSize: 13, margin: "6px 4px 0" }}>
          Solid arrows are request/response; dashed vermilion arrows are asynchronous events.
        </p>
      </div>

      <ol className="steps">
        <li>
          <b>Ask for an upload URL.</b> The dashboard calls <code>POST /api/uploads</code>. A Lambda function checks
          type, size and the daily quota, writes a <i>waiting</i> record and returns a presigned S3 POST that S3 itself
          will enforce (type, max 10 MB).
        </li>
        <li>
          <b>Upload straight to S3.</b> The browser posts the file to the bucket. No file byte passes through API
          Gateway or Lambda, so there is no 6 MB payload limit and no compute billed for transfers.
        </li>
        <li>
          <b>S3 fires an event.</b> <code>ObjectCreated</code> goes to an SQS queue that buffers bursts and retries
          failures; after three failed attempts a message moves to a dead-letter queue, which raises an alarm.
        </li>
        <li>
          <b>Lambda scales with the queue.</b> The event source mapping hands the worker batches of up to five
          messages and runs at most five copies at once, which keeps Textract under its request quota.
        </li>
        <li>
          <b>Textract reads the bill.</b> AnalyzeExpense returns vendor, date, totals, tax lines and line items with
          confidences. Parchi picks the right candidates (CGST + SGST, Grand Total over Total Qty).
        </li>
        <li>
          <b>Checked and filed.</b> The GSTIN checksum, subtotal + tax = total and a plausible date are verified;
          anything doubtful is flagged for review. One conditional write files it in DynamoDB.
        </li>
        <li>
          <b>SNS emails you.</b> One topic for everyone; each subscription has a filter policy on the owner
          attribute, so you only get your own confirmations.
        </li>
        <li>
          <b>Watch it happen.</b> CloudFront serves this dashboard from a private S3 bucket and routes{" "}
          <code>/api/*</code> to API Gateway on the same domain, so there is no CORS to configure.
        </li>
      </ol>

      <div className="grid-2" style={{ marginTop: 26 }}>
        <div className="panel card">
          <h3>What 1,000 documents cost</h3>
          <table className="cost">
            <tbody>
              {COST.map(([service, basis, cost]) => (
                <tr key={service}>
                  <td>
                    {service}
                    <div className="muted" style={{ fontSize: 12.5 }}>
                      {basis}
                    </div>
                  </td>
                  <td className="r">{cost}</td>
                </tr>
              ))}
              <tr className="total">
                <td>Total, about</td>
                <td className="r">$10.10</td>
              </tr>
            </tbody>
          </table>
          <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>
            Approximate on-demand list prices (us-east-1, late 2026; ap-south-1 is similar). OCR is ~99% of the bill;
            with nothing uploaded the stack costs nothing beyond a few cents of storage.
          </p>
        </div>
        <div className="panel card">
          <h3>Built to fail safely</h3>
          <ul style={{ paddingLeft: 18, margin: 0 }}>
            <li>Idempotent: a duplicate SQS delivery finds the document already filed (or leased) and is skipped.</li>
            <li>Partial batch failures: one bad file never makes SQS redeliver the good ones.</li>
            <li>Unreadable files fail at once with a reason; throttling is retried, then dead-lettered and alarmed.</li>
            <li>Least privilege: each function has its own IAM role with only the actions it uses.</li>
            <li>Daily upload caps (per workspace and global) enforced by a DynamoDB transaction.</li>
            <li>Metrics in CloudWatch Embedded Metric Format, a dashboard, alarms and a monthly budget alert.</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
