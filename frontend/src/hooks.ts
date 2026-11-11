import { useCallback, useEffect, useRef, useState } from "react";

export type Route =
  | { name: "home" }
  | { name: "doc"; id: string }
  | { name: "how" }
  | { name: "join"; key: string };

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#/, "");
  const doc = /^\/doc\/([0-9A-Za-z]+)$/.exec(path);
  if (doc) return { name: "doc", id: doc[1] };
  const join = /^\/join\/([A-Za-z0-9_-]+)$/.exec(path);
  if (join) return { name: "join", key: join[1] };
  if (path === "/how") return { name: "how" };
  return { name: "home" };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => {
      setRoute(parseHash(window.location.hash));
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

/**
 * Load data now and keep refreshing it. `interval` may change between renders
 * (fast while something is processing, slow when idle); polling pauses while the tab is hidden.
 */
export function usePolling<T>(load: () => Promise<T>, interval: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;

  const refresh = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (caught) {
      setError(caught as Error);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      if (!document.hidden) await refresh();
      if (!cancelled) timer = window.setTimeout(tick, interval);
    };
    tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interval, refresh, ...deps]);

  return { data, error, refresh, setData };
}
