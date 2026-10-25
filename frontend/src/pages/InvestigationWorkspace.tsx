import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAction, useApi } from "../hooks/useApi";
import { api, qs } from "../services/api";
import type { Candidate, Investigation, Patch } from "../types/api";
import { Banner, Empty, ErrorBanner, EpistemicLegend, Loading, Mark, PageHead, Panel, StatusMark, fmt } from "../components/ui";
import { CodeViewer } from "../components/CodeViewer";

const METHODS = ["hybrid_dependency", "hybrid_conventional", "lexical", "semantic", "dependency_aware", "adaptive"];
interface FileContent { language: string; content: string }

export default function InvestigationWorkspace() {
  const id = Number(useParams().id);
  const q = useApi<Investigation>(`/investigations/${id}`);
  const [method, setMethod] = useState("hybrid_dependency");
  const [selected, setSelected] = useState<Candidate | null>(null);
  const nav = useNavigate();
  const retrieve = useAction(async () => { await api.post(`/investigations/${id}/retrieve`, { options: { method } }); q.reload(); });
  const [fresh, setFresh] = useState<Investigation["analysis"] | null>(null);
  const analyze = useAction(async () => { const r = await api.post<Investigation>(`/investigations/${id}/analyze`, { use_model: true }); setFresh(r.analysis ?? null); q.reload(); });
  const makePatch = useAction(async () => { const p = await api.post<Patch>(`/investigations/${id}/patches`, { from_analysis: true }); nav(`/patches/${p.id}`); });
  const inv = q.data;
  const repoId = inv?.repository_id;
  const file = useApi<FileContent>(selected && repoId ? `/repositories/${repoId}/file${qs({ path: selected.file_path })}` : null);
  if (!inv) return <>{q.error ? <ErrorBanner error={q.error} /> : <Loading />}</>;
  const cands = inv.candidates ?? [];
  const hyps = inv.hypotheses ?? [];
  const analysis = fresh ?? inv.analysis;
  return (
    <>
      <PageHead title={inv.title} sub={<StatusMark s={inv.status} />}
        actions={<div className="row">
          <select value={method} onChange={(e) => setMethod(e.target.value)} aria-label="Retrieval method">{METHODS.map((m) => <option key={m}>{m}</option>)}</select>
          <button className="primary" disabled={retrieve.pending} onClick={() => retrieve.run()}>{retrieve.pending ? "Ranking…" : cands.length ? "Re-rank" : "Rank suspects"}</button>
          <button disabled={analyze.pending || cands.length === 0} onClick={() => analyze.run()}>{analyze.pending ? "Analyzing…" : "Analyze"}</button>
        </div>} />
      <ErrorBanner error={q.error ?? retrieve.error ?? analyze.error ?? makePatch.error} />
      <EpistemicLegend />
      <div className="grid2">
        <Panel title="Bug report">
          <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{inv.bug_report?.description || <span className="sub">no description</span>}</p>
          {inv.bug_report?.stack_trace && <pre className="code" style={{ marginTop: 8 }}>{inv.bug_report.stack_trace}</pre>}
          {(inv.bug_report?.failing_tests?.length ?? 0) > 0 && <><h3 style={{ marginTop: 10 }}>Failing tests (given by you)</h3><ul className="plain mono">{inv.bug_report!.failing_tests.map((t) => <li key={t}>{t}</li>)}</ul></>}
        </Panel>
        <Panel title="Retrieval run">
          {inv.retrieval_run ? <dl className="kv"><dt>method</dt><dd>{inv.retrieval_run.method}</dd><dt>index version</dt><dd>{inv.retrieval_run.index_version}</dd><dt>runtime</dt><dd>{fmt(inv.retrieval_run.runtime_ms, 1)} ms</dd><dt>context size</dt><dd>{inv.retrieval_run.context_chars} chars</dd></dl> : <Empty title="Not ranked yet">Choose a method and press “Rank suspects”.</Empty>}
          {inv.warnings?.map((w) => <Banner key={w} kind="warn">{w}</Banner>)}
        </Panel>
      </div>

      <Panel title="Ranked suspect locations" actions={cands.length > 0 && <Mark kind="heuristic">heuristic ranking, not a proven root cause</Mark>} pad={false}>
        {cands.length === 0 ? <Empty title="No suspects yet" /> : (
          <table className="t"><thead><tr><th>#</th><th>Location</th><th>Confidence</th><th className="num">Score</th><th>Related tests</th></tr></thead><tbody>
            {cands.map((c) => (
              <tr key={c.id} className={`click ${selected?.id === c.id ? "sel" : ""}`} onClick={() => setSelected(c)}>
                <td>{c.rank}</td><td className="mono">{c.file_path}{c.symbol_name ? ` · ${c.symbol_name}` : ""}:{c.start_line}-{c.end_line}</td>
                <td><Mark kind="heuristic">{c.confidence_label} (uncalibrated)</Mark></td><td className="num">{fmt(c.score)}</td><td className="mono">{c.related_tests.slice(0, 2).join(", ") || "–"}</td>
              </tr>))}
          </tbody></table>)}
      </Panel>

      {selected && (
        <Panel title={<span className="mono">Evidence · {selected.file_path}:{selected.start_line}-{selected.end_line}</span>}>
          <div className="grid2">
            <div>
              <h3>Signals <Mark kind="heuristic">heuristic</Mark></h3>
              <div className="row" style={{ marginBottom: 8 }}>{Object.entries(selected.signals).filter(([, v]) => v > 0).map(([k, v]) => <Mark key={k} kind="heuristic">{k} {fmt(v, 2)}</Mark>)}</div>
              <h3>Evidence <Mark kind="fact">fact</Mark></h3><ul className="plain">{selected.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
              <h3 style={{ marginTop: 8 }}>Uncertainty</h3><ul className="plain">{selected.uncertainty.map((e, i) => <li key={i}>{e}</li>)}</ul>
            </div>
            <div>{file.data ? <CodeViewer value={file.data.content} language={file.data.language} highlight={[selected.start_line, selected.end_line]} height={300} /> : <Loading />}</div>
          </div>
        </Panel>)}

      <Panel title="Analysis">
        {!analysis ? <Empty title="Not analyzed yet">“Analyze” uses the configured model if there is one, and a deterministic summary otherwise.</Empty> : (
          <div className="col">
            <div className="row"><Mark kind={analysis.origin === "model" ? "model" : "fact"}>{analysis.origin === "model" ? `model: ${analysis.model ?? "unknown"}` : "deterministic (no model)"}</Mark>{!analysis.provider_available && <span className="sub">No model provider is configured. Set one in Settings to get AI hypotheses.</span>}</div>
            <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{analysis.summary}</p>
            {analysis.errors.length > 0 && <Banner kind="warn">{analysis.errors.join("; ")}</Banner>}
            {analysis.unverified.length > 0 && <div><h3>Not verified</h3><ul className="plain">{analysis.unverified.map((u, i) => <li key={i}>{u}</li>)}</ul></div>}
          </div>)}
      </Panel>

      {hyps.length > 0 && <Panel title="Hypotheses" pad={false}>
        {hyps.map((h) => (
          <div key={h.id} style={{ padding: 12, borderBottom: "1px solid var(--rule-2)" }}>
            <div className="row"><Mark kind={h.origin === "model" ? "model" : "fact"}>{h.origin}</Mark><b>{h.statement}</b></div>
            {h.supporting.length > 0 && <p className="sub">Supporting: {h.supporting.join("; ")}</p>}
            {h.contradicting.length > 0 && <p className="sub">Contradicting: {h.contradicting.join("; ")}</p>}
            <div className="row">{h.citations.map((c, i) => <Mark key={i} kind="fact">{c.path}:{c.start_line}-{c.end_line}</Mark>)}
              {h.rejected_citations.map((c, i) => <Mark key={`r${i}`} kind="fail" >rejected citation {c.path}:{c.start_line}</Mark>)}</div>
          </div>))}
      </Panel>}

      <Panel title="Patches" actions={analysis?.proposed_patch && <button className="primary" disabled={makePatch.pending} onClick={() => makePatch.run()}>Create patch from analysis</button>}>
        {(inv.patches?.length ?? 0) === 0 ? <Empty title="No patches">A patch is only a proposal until its tests pass in the sandbox.</Empty> : (
          <table className="t"><tbody>{inv.patches!.map((p) => <tr key={p.id}><td><Link to={`/patches/${p.id}`}>patch {p.id}</Link></td><td><StatusMark s={p.status} /></td><td className="mono">{p.changed_files.join(", ")}</td></tr>)}</tbody></table>)}
      </Panel>
    </>
  );
}
