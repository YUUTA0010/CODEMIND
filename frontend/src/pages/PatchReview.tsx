import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";
import type { Patch, PatchReport } from "../types/api";
import { Banner, Empty, ErrorBanner, Loading, Mark, PageHead, Panel, StatusMark, fmt } from "../components/ui";

export default function PatchReview() {
  const id = Number(useParams().id);
  const q = useApi<Patch>(`/patches/${id}`, { refreshOn: ["verify."] });
  const verify = useAction(async () => { await api.post<PatchReport>(`/patches/${id}/verify`, {}); q.reload(); });
  const exportP = useAction(async (apply: boolean) => { await api.post(`/patches/${id}/export`, { approve: true, apply_to_working_tree: apply }); q.reload(); });
  const [confirmApply, setConfirmApply] = useState(false);
  const p = q.data;
  if (!p) return <>{q.error ? <ErrorBanner error={q.error} /> : <Loading />}</>;
  const rep = p.report;
  const passed = p.status === "TESTS_PASSED";
  return (
    <>
      <PageHead title={`Patch ${p.id}`} sub={<><StatusMark s={p.status} /> · <Link to={`/investigations/${p.investigation_id}`}>investigation {p.investigation_id}</Link></>}
        actions={<button className="primary" disabled={verify.pending} onClick={() => verify.run()}>{verify.pending ? "Verifying in sandbox…" : rep ? "Verify again" : "Verify in sandbox"}</button>} />
      <ErrorBanner error={q.error ?? verify.error ?? exportP.error} />
      <Banner kind="warn">A generated patch is a proposal. It counts as verified only if the sandbox tests below passed, and passing tests do not prove the bug is fully fixed.</Banner>
      <div className="grid2">
        <Panel title="Rationale"><div className="row"><Mark kind={p.model_name ? "model" : "neutral"}>{p.model_name ? `model: ${p.model_name}` : "supplied by user"}</Mark></div><p style={{ whiteSpace: "pre-wrap" }}>{p.rationale || "no rationale given"}</p>
          <h3>Changed files</h3><ul className="plain mono">{p.changed_files.map((f) => <li key={f}>{f}</li>)}</ul></Panel>
        <Panel title="Sandbox">
          {!rep ? <Empty title="Not verified yet">Verification runs a baseline, the patched targeted tests, then the broad suite, in a disposable copy.</Empty> : (
            <dl className="kv"><dt>isolation</dt><dd>{rep.isolation.join(", ") || "–"}</dd><dt>mode</dt><dd>{rep.sandbox_modes.join(", ")}</dd><dt>possible regression</dt><dd>{rep.possible_regression ? <Mark kind="fail">yes</Mark> : "none observed"}</dd></dl>)}
        </Panel>
      </div>
      <Panel title="Diff" pad={false}><pre className="code" style={{ maxHeight: 420, border: 0 }}>{(p.diff ?? "").split("\n").map((l, i) => <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "diffline-add" : l.startsWith("-") && !l.startsWith("---") ? "diffline-del" : undefined}>{l || " "}</div>)}</pre></Panel>
      {rep && <>
        <Panel title="Test executions" pad={false}><table className="t"><thead><tr><th>Phase</th><th>Outcome</th><th className="num">Passed</th><th className="num">Failed</th><th className="num">Seconds</th><th>Command</th></tr></thead><tbody>
          {rep.executions.map((e) => <tr key={e.id}><td>{e.phase}</td><td><StatusMark s={e.outcome} /></td><td className="num">{e.tests_passed ?? "–"}/{e.tests_total ?? "–"}</td><td className="num">{e.tests_failed ?? "–"}</td><td className="num">{fmt(e.runtime_seconds, 2)}</td><td className="mono">{e.command}</td></tr>)}</tbody></table></Panel>
        <div className="grid2">
          <Panel title={<>Observed <Mark kind="fact">fact</Mark></>}><ul className="plain">{rep.observed_facts.map((f, i) => <li key={i}>{f}</li>)}</ul>{rep.notes.length > 0 && <ul className="plain">{rep.notes.map((f, i) => <li key={i}>{f}</li>)}</ul>}</Panel>
          <Panel title="What this verification does not show"><ul className="plain">{rep.verification_limits.map((f, i) => <li key={i}>{f}</li>)}</ul></Panel>
        </div></>}
      <Panel title="Export">
        {p.export_approved && <p><Mark kind="pass">approved</Mark> {p.exported_to && <span className="mono">exported to {p.exported_to}</span>}</p>}
        <div className="row">
          <button disabled={exportP.pending} onClick={() => exportP.run(false)}>Approve and export as .patch file</button>
          <label className="row"><input type="checkbox" checked={confirmApply} onChange={(e) => setConfirmApply(e.target.checked)} /> I understand this modifies my working tree</label>
          <button className="danger" disabled={exportP.pending || !confirmApply} onClick={() => exportP.run(true)}>Apply to working tree</button>
        </div>
        {!passed && <p className="sub" style={{ marginTop: 8 }}>This patch has not passed verification (status: {p.status.toLowerCase().replaceAll("_", " ")}). You can still export it, at your own judgment.</p>}
      </Panel>
    </>
  );
}
