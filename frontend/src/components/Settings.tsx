import { useEffect, useState } from "react";
import { api, type Health, newKey, type OutboxMail, setWorkspaceKey, type Subscription, workspaceKey } from "../api";
import { relativeTime } from "../format";

export function Settings({ health, onClose }: { health: Health | null; onClose: () => void }) {
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "info" | "err"; text: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [mail, setMail] = useState<OutboxMail[]>([]);
  const local = health?.stage === "local";

  useEffect(() => {
    api.subscription().then((value) => {
      setSubscription(value);
      setEmail(value.email ?? "");
    }, () => undefined);
  }, []);

  useEffect(() => {
    if (!local) return;
    const load = () => api.outbox().then((value) => setMail(value.items), () => undefined);
    load();
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [local]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const subscribe = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const value = await api.subscribe(email.trim());
      setSubscription(value);
      setMessage({
        kind: "info",
        text:
          value.status === "confirmed"
            ? "You will get an email for every document."
            : "AWS has sent a confirmation email. Click its link, then emails start arriving.",
      });
    } catch (error) {
      setMessage({ kind: "err", text: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.unsubscribe();
      setSubscription({ email: null, status: "none" });
      setEmail("");
    } finally {
      setBusy(false);
    }
  };

  const link = `${window.location.origin}${window.location.pathname}#/join/${workspaceKey()}`;

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Settings">
        <button className="close" onClick={onClose} aria-label="Close settings">
          ×
        </button>
        <h2>Settings</h2>
        <p className="muted" style={{ marginTop: 0 }}>Your workspace lives in this browser. No account needed.</p>

        <section>
          <h3 style={{ margin: "0 0 6px", fontFamily: "var(--serif)", fontWeight: 500 }}>Email confirmations</h3>
          <p className="muted" style={{ fontSize: 14, marginTop: 0 }}>
            One SNS topic serves everyone; your subscription carries a filter policy so you only get your own
            documents.
          </p>
          <form className="inline-form" onSubmit={subscribe}>
            <input
              type="email"
              required
              placeholder="you@example.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-label="Email address"
            />
            <button className="btn primary" disabled={busy}>
              {subscription?.email !== email.trim() || subscription?.status === "none"
                ? "Subscribe"
                : subscription.status === "pending"
                  ? "Resend"
                  : "Update"}
            </button>
          </form>
          {subscription?.email && (
            <p style={{ fontSize: 14, marginBottom: 0 }}>
              {subscription.email}:{" "}
              <b style={{ color: subscription.status === "confirmed" ? "var(--ok)" : "var(--warn)" }}>
                {subscription.status === "confirmed" ? "confirmed" : subscription.status === "pending" ? "waiting for you to confirm" : "not subscribed"}
              </b>{" "}
              ·{" "}
              <button className="chip-button" onClick={remove} disabled={busy}>
                remove
              </button>
            </p>
          )}
          {message && <div className={`notice ${message.kind}`}>{message.text}</div>}
        </section>

        {local && (
          <section>
            <h3 style={{ margin: "0 0 6px", fontFamily: "var(--serif)", fontWeight: 500 }}>Outbox (local mode)</h3>
            <p className="muted" style={{ fontSize: 14, marginTop: 0 }}>
              Running offline, so SNS can't deliver email. These are the messages it would send to you.
            </p>
            {mail.length === 0 && <p className="muted">Nothing yet. Set an email above, then upload a document.</p>}
            {mail.map((item) => (
              <div className="mail" key={item.id}>
                <strong>{item.subject}</strong>
                <span className="muted" style={{ fontSize: 12 }}>
                  to {item.to ?? "you"} · {relativeTime(item.sentAt)}
                </span>
                <pre>{item.body}</pre>
              </div>
            ))}
          </section>
        )}

        <section>
          <h3 style={{ margin: "0 0 6px", fontFamily: "var(--serif)", fontWeight: 500 }}>Workspace</h3>
          <p className="muted" style={{ fontSize: 14, marginTop: 0 }}>
            Your documents are tied to a random key stored in this browser (the server keeps only its hash). Open
            this private link on another device to see the same ledger. Anyone with the link can too.
          </p>
          <div className="inline-form">
            <input readOnly value={link} aria-label="Workspace link" onFocus={(event) => event.target.select()} />
            <button
              className="btn"
              onClick={() => navigator.clipboard?.writeText(link).then(() => setCopied(true), () => undefined)}
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <p style={{ marginBottom: 0 }}>
            <button
              className="chip-button"
              onClick={() => {
                if (confirm("Start an empty workspace? Keep the link above if you want to come back to this one.")) {
                  setWorkspaceKey(newKey());
                  window.location.hash = "#/";
                  window.location.reload();
                }
              }}
            >
              Start a new workspace
            </button>
          </p>
        </section>

        <section className="muted" style={{ fontSize: 13 }}>
          {health
            ? `API ${health.version} · stage ${health.stage} · ${health.region} · OCR: ${health.engine}`
            : "The API is not reachable."}
        </section>
      </aside>
    </>
  );
}
