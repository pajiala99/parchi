import { useEffect, useState } from "react";
import { api, type Health, isValidKey, setWorkspaceKey } from "./api";
import { Settings } from "./components/Settings";
import { useRoute } from "./hooks";
import { Dashboard } from "./views/Dashboard";
import { DocumentView } from "./views/DocumentView";
import { HowItWorks } from "./views/HowItWorks";

export default function App() {
  const route = useRoute();
  const [health, setHealth] = useState<Health | null>(null);
  const [healthFailed, setHealthFailed] = useState(false);
  const [settings, setSettings] = useState(false);

  useEffect(() => {
    api.health().then(setHealth, () => setHealthFailed(true));
  }, []);

  useEffect(() => {
    if (route.name === "join") {
      if (isValidKey(route.key)) setWorkspaceKey(route.key);
      window.location.replace(`${window.location.pathname}#/`);
      window.location.reload();
    }
  }, [route]);

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="#/">
            <span className="brand-mark">
              parchi<span>.</span>
            </span>
            <span className="brand-sub">serverless document pipeline</span>
          </a>
          <nav className="nav">
            <a href="#/" aria-current={route.name === "home" || route.name === "doc" ? "page" : undefined}>
              Ledger
            </a>
            <a href="#/how" aria-current={route.name === "how" ? "page" : undefined}>
              How it works
            </a>
            <button onClick={() => setSettings(true)}>Settings</button>
            <span className={`env${healthFailed ? " down" : ""}`} title="API status">
              <b>●</b>{" "}
              {health ? `${health.stage} · ${health.region} · ${health.engine}` : healthFailed ? "API offline" : "connecting"}
            </span>
          </nav>
        </div>
      </header>
      <main className="shell">
        {route.name === "doc" ? <DocumentView id={route.id} /> : route.name === "how" ? <HowItWorks /> : <Dashboard health={health} />}
      </main>
      <footer>
        Parchi · UCS531 Cloud Computing project, Thapar Institute of Engineering &amp; Technology · Hardik Pajiala,
        Shikhar Goel, Rumnik Dhillon ·{" "}
        <a href="https://github.com/pajiala99/parchi" target="_blank" rel="noreferrer">
          source on GitHub
        </a>
      </footer>
      {settings && <Settings health={health} onClose={() => setSettings(false)} />}
    </>
  );
}
