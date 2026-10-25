import { useCallback, useEffect, useRef, useState } from "react";
import { api, describeError } from "../services/api";
import { subscribe, type AppEvent } from "../services/events";

export interface Query<T> { data: T | null; error: string | null; loading: boolean; reload: () => void }

/** Fetch `path` (null = skip). Refetches when `path` changes, on reload(), and when a matching live event arrives. */
export function useApi<T>(path: string | null, opts: { refreshOn?: string[] } = {}): Query<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (path === null) { setData(null); setLoading(false); return; }
    const ctl = new AbortController();
    setLoading(true);
    api.get<T>(path, ctl.signal)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => { if ((e as Error).name !== "AbortError") setError(describeError(e)); })
      .finally(() => { if (!ctl.signal.aborted) setLoading(false); });
    return () => ctl.abort();
  }, [path, tick]);

  const prefixes = opts.refreshOn?.join("|") ?? "";
  useEffect(() => {
    if (!prefixes) return;
    const list = prefixes.split("|");
    return subscribe((ev: AppEvent) => { if (list.some((p) => ev.type.startsWith(p))) reload(); });
  }, [prefixes, reload]);

  return { data, error, loading, reload };
}

/** Run an async action with pending/error state (for buttons that call POST endpoints). */
export function useAction<A extends unknown[], R>(fn: (...a: A) => Promise<R>) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const run = useCallback(async (...a: A): Promise<R | undefined> => {
    setPending(true); setError(null);
    try { return await fn(...a); } catch (e) { if (alive.current) setError(describeError(e)); return undefined; }
    finally { if (alive.current) setPending(false); }
  }, [fn]);
  return { run, pending, error, clearError: () => setError(null) };
}
