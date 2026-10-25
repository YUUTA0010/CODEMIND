import type { ReactNode } from "react";

export type MarkKind = "fact" | "heuristic" | "model" | "pass" | "fail" | "warn" | "neutral";
const MARK_HELP: Record<string, string> = {
  fact: "Observed fact: read directly from the index, the parser or a test run.",
  heuristic: "Heuristic score: a ranking signal, not a proven cause and not calibrated.",
  model: "Model-generated text: unverified until checked against code and tests.",
};
export function Mark({ kind, children }: { kind: MarkKind; children: ReactNode }) {
  return <span className={`mark ${kind}`} title={MARK_HELP[kind]}>{children}</span>;
}
export const EpistemicLegend = () => (
  <div className="legend" aria-label="Legend">
    <span><Mark kind="fact">fact</Mark> read from index or tests</span>
    <span><Mark kind="heuristic">heuristic</Mark> ranking signal, not proof</span>
    <span><Mark kind="model">model</Mark> unverified AI text</span>
  </div>
);

export function statusKind(s: string): MarkKind {
  if (["TESTS_PASSED", "COMPLETED", "PASSED", "ANALYZED", "READY", "INDEXED"].includes(s)) return "pass";
  if (["TESTS_FAILED", "FAILED", "PATCH_REJECTED", "TESTS_TIMED_OUT", "ERROR"].includes(s)) return "fail";
  if (["VERIFICATION_INCOMPLETE", "RUNNING", "QUEUED", "CANCELLED", "PATCH_GENERATED", "PATCH_APPLIED_IN_SANDBOX"].includes(s)) return "warn";
  return "neutral";
}
export const StatusMark = ({ s }: { s: string }) => <Mark kind={statusKind(s)}>{s.replaceAll("_", " ").toLowerCase()}</Mark>;

export const Empty = ({ title, children }: { title: string; children?: ReactNode }) => <div className="empty"><b>{title}</b>{children}</div>;
export const Banner = ({ kind = "info", children }: { kind?: "info" | "warn" | "error" | "demo"; children: ReactNode }) => <div className={`banner ${kind}`} role={kind === "error" ? "alert" : undefined}>{children}</div>;
export const ErrorBanner = ({ error }: { error: string | null }) => (error ? <Banner kind="error">{error}</Banner> : null);
export const Loading = () => <div className="empty">Loading…</div>;

export function Panel({ title, actions, children, pad = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; pad?: boolean }) {
  return <section className="panel">{title !== undefined && <div className="hd">{title}<span className="sp" />{actions}</div>}<div className={pad ? "bd" : undefined}>{children}</div></section>;
}
export function PageHead({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return <div className="page-head"><div><h1>{title}</h1>{sub && <p className="sub">{sub}</p>}</div><span className="sp" />{actions}</div>;
}
export const Progress = ({ value, max }: { value: number; max: number }) => <div className="bar" role="progressbar" aria-valuenow={value} aria-valuemax={max}><i style={{ width: `${max ? Math.min(100, (100 * value) / max) : 0}%` }} /></div>;

export const fmt = (n: number | null | undefined, d = 3) => (n === null || n === undefined || Number.isNaN(n) ? "–" : n.toFixed(d));
export const when = (s: string | null | undefined) => (s ? new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z").toLocaleString() : "–");

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string }[]; value: T; onChange: (t: T) => void }) {
  return <div className="tabs" role="tablist">{tabs.map((t) => <button key={t.id} role="tab" aria-selected={value === t.id} className={value === t.id ? "on" : ""} onClick={() => onChange(t.id)}>{t.label}</button>)}</div>;
}

export function download(name: string, text: string, mime = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a"); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
}
