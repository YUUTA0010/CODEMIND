/** Thin typed fetch wrapper. Backend errors arrive as {error:{code,message,details}} and are surfaced as ApiError. */
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

const TOKEN_KEY = "codemind.token";
export const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; } };
export const setToken = (t: string) => { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ } };

export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    const d = Array.isArray(e.details) ? ` (${e.details.map((x: any) => `${(x.loc ?? []).slice(1).join(".")}: ${x.msg}`).join("; ")})` : "";
    return `${e.message}${d}`;
  }
  return e instanceof Error ? e.message : String(e);
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network", "Cannot reach the CodeMind backend. Start it with `python scripts/run_backend.py`.");
  }
  if (!res.ok) {
    let err: any = {};
    try { err = (await res.json()).error ?? {}; } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, err.code ?? "http_error", err.message ?? `Request failed (${res.status})`, err.details);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const api = {
  get: <T,>(path: string, signal?: AbortSignal) => request<T>("GET", path, undefined, signal),
  post: <T,>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T,>(path: string, body: unknown) => request<T>("PUT", path, body),
};

export const qs = (params: Record<string, string | number | boolean | undefined | null | string[]>): string => {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => u.append(k, x)); else u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
};
