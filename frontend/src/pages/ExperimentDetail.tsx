import { useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ErrorBar, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../hooks/useApi";
import type { ExperimentResults, ExperimentSummary } from "../types/api";
import { Banner, Empty, ErrorBanner, Loading, Mark, PageHead, Panel, Progress, StatusMark, download, fmt } from "../components/ui";

const METRICS = [["top1", "Top-1"], ["top5", "Top-5"], ["mrr", "MRR"], ["recall@5", "Recall@5"]] as const;

export default function ExperimentDetail() {
  const id = Number(useParams().id);
  const q = useApi<ExperimentResults>(`/experiments/${id}/results`, { refreshOn: ["experiment."] });
  const r = q.data;
  if (!r) return <>{q.error ? <ErrorBanner error={q.error} /> : <Loading />}</>;
  const e = r.experiment;
  const s = (r.summary && "methods" in r.summary ? r.summary : null) as ExperimentSummary | null;
  const methods = s ? Object.keys(s.methods) : [];
  const chart = METRICS.map(([k, label]) => {
    const row: Record<string, any> = { metric: label };
    methods.forEach((m) => { const ci = (s!.methods[m] as any)[k]; if (ci) { row[m] = ci.mean; row[`${m}__err`] = [ci.mean - ci.ci_low, ci.ci_high - ci.mean]; } });
    return row;
  });
  const palette = ["#2457b5", "#0d7a68", "#7a3b8f", "#b3261e", "#8a5a00", "#4a5663", "#17803d", "#c2479a", "#5b8c00", "#00839c"];
  return (
    <>
      <PageHead title={e.name} sub={<><StatusMark s={e.status} /> · seed {e.seed} · dataset {e.dataset?.name}</>}
        actions={r.downloads.length > 0 && <div className="row">{r.downloads.map((u) => <a key={u} className="btn" href={u} download>{u.split("/").pop()}</a>)}
          <button onClick={() => download(`experiment-${id}-config.json`, JSON.stringify((r as any).config ?? {}, null, 2), "application/json")}>config.json</button></div>} />
      {e.synthetic_notice && <Banner kind="demo">{e.synthetic_notice}</Banner>}
      <ErrorBanner error={q.error ?? e.error} />
      {e.status === "RUNNING" && <Panel><div className="row"><Progress value={e.progress} max={e.total} /><span>{e.progress}/{e.total} method-task evaluations</span></div></Panel>}
      {!s ? (e.status === "RUNNING" || e.status === "QUEUED" ? null : <Panel><Empty title="No summary available" /></Panel>) : (
        <>
          {s.sample_size_warning && <Banner kind="warn"><b>Inconclusive by design.</b> {s.sample_size_warning}</Banner>}
          <Panel title={<>Localization metrics, mean with 95% bootstrap CI <Mark kind="fact">fact</Mark></>}>
            <div style={{ height: 300 }}><ResponsiveContainer><BarChart data={chart}><CartesianGrid strokeDasharray="3 3" stroke="var(--rule)" /><XAxis dataKey="metric" stroke="var(--ink-2)" /><YAxis domain={[0, 1]} stroke="var(--ink-2)" /><Tooltip /><Legend />
              {methods.map((m, i) => <Bar key={m} dataKey={m} fill={palette[i % palette.length]} isAnimationActive={false}><ErrorBar dataKey={`${m}__err`} width={3} stroke="var(--ink)" /></Bar>)}
            </BarChart></ResponsiveContainer></div>
          </Panel>
          <Panel title={`Per-method results (n = ${s.n_tasks} tasks)`} pad={false}>
            <table className="t"><thead><tr><th>Method</th>{METRICS.map(([, l]) => <th key={l} className="num">{l}</th>)}<th className="num">Irrelevant files</th><th className="num">Context chars</th><th className="num">Median latency ms</th></tr></thead><tbody>
              {methods.map((m) => { const x = s.methods[m] as any; const c = (k: string) => x[k] ? `${fmt(x[k].mean, 2)} [${fmt(x[k].ci_low, 2)}, ${fmt(x[k].ci_high, 2)}]` : "–";
                return <tr key={m}><td>{m}{m === s.reference_method && <> <Mark kind="neutral">reference</Mark></>}</td>{METRICS.map(([k]) => <td key={k} className="num mono">{c(k)}</td>)}<td className="num">{fmt(x.irrelevant_files?.mean, 1)}</td><td className="num">{fmt(x.context_chars?.mean, 0)}</td><td className="num">{fmt(x.latency_ms_median, 1)}</td></tr>; })}
            </tbody></table>
          </Panel>
          <Panel title={`Paired difference vs reference (${s.reference_method})`} pad={false}>
            <table className="t"><thead><tr><th>Method</th><th>Metric</th><th className="num">Mean diff</th><th className="num">95% CI</th><th className="num">p (sign-flip)</th><th className="num">W/L/T</th><th>Reading</th></tr></thead><tbody>
              {Object.entries(s.paired_vs_reference).flatMap(([m, per]) => Object.entries(per).filter(([k]) => ["top1", "mrr"].includes(k)).map(([k, d]) => (
                <tr key={`${m}-${k}`}><td>{m}</td><td>{k}</td><td className="num">{fmt(d.mean_diff, 3)}</td><td className="num mono">[{fmt(d.ci_low, 2)}, {fmt(d.ci_high, 2)}]</td><td className="num">{fmt(d.p_value, 3)}</td><td className="num">{d.wins}/{d.losses}/{d.ties}</td><td>{d.interpretation}</td></tr>)))}
            </tbody></table>
          </Panel>
          {methods.some((m) => s.methods[m].repair_skipped) && <Banner kind="info">Repair stage skipped: {s.methods[methods.find((m) => s.methods[m].repair_skipped)!].repair_skipped}</Banner>}
          {s.notes && s.notes.length > 0 && <Panel title="Notes"><ul className="plain">{s.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></Panel>}
        </>)}
    </>
  );
}
