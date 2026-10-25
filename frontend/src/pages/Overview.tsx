import { Link } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import type { Dashboard } from "../types/api";
import { Empty, ErrorBanner, Loading, PageHead, Panel, Progress, StatusMark, when } from "../components/ui";

export default function Overview() {
  const q = useApi<Dashboard>("/dashboard", { refreshOn: ["index.", "verify.", "experiment."] });
  const d = q.data;
  return (
    <>
      <PageHead title="Overview" sub="Repositories, active work and recent verification outcomes, read live from the backend." />
      <ErrorBanner error={q.error} />
      {q.loading && !d && <Loading />}
      {d && (
        <>
          <div className="grid2">
            <Panel title="Repositories" actions={<Link to="/repositories">manage</Link>} pad={false}>
              {d.repositories.length === 0 ? <Empty title="No repositories yet">Register a local folder on the Repositories page to index it.</Empty> : (
                <table className="t"><thead><tr><th>Name</th><th>Status</th><th className="num">Files</th><th className="num">Index v</th></tr></thead><tbody>
                  {d.repositories.map((r) => <tr key={r.id}><td><Link to={`/repositories/${r.id}`}>{r.name}</Link></td><td><StatusMark s={r.status} /></td><td className="num">{r.file_count}</td><td className="num">{r.index_version}</td></tr>)}
                </tbody></table>)}
            </Panel>
            <Panel title="Active indexing" pad={d.active_indexing_jobs.length === 0}>
              {d.active_indexing_jobs.length === 0 ? <Empty title="Nothing is indexing" /> : d.active_indexing_jobs.map((j) => (
                <div key={j.id} className="row" style={{ padding: 10 }}><span>repo {j.repository_id}</span><Progress value={j.progress} max={j.total} /><span className="mono">{j.progress}/{j.total} {j.message}</span></div>))}
            </Panel>
          </div>
          <div className="grid2">
            <Panel title="Recent investigations" actions={<Link to="/investigations">all</Link>} pad={false}>
              {d.recent_investigations.length === 0 ? <Empty title="No investigations yet">Describe a bug on the Investigations page.</Empty> : (
                <table className="t"><tbody>{d.recent_investigations.map((i) => <tr key={i.id}><td><Link to={`/investigations/${i.id}`}>{i.title}</Link></td><td><StatusMark s={i.status} /></td><td>{when(i.created_at)}</td></tr>)}</tbody></table>)}
            </Panel>
            <Panel title="Recent patch verifications" pad={false}>
              {d.recent_verifications.length === 0 ? <Empty title="No verified patches yet">A patch counts as verified only after sandboxed tests ran.</Empty> : (
                <table className="t"><tbody>{d.recent_verifications.map((v) => <tr key={v.patch_id}><td><Link to={`/patches/${v.patch_id}`}>patch {v.patch_id}</Link></td><td><StatusMark s={v.status} /></td><td className="mono">{v.files.join(", ")}</td></tr>)}</tbody></table>)}
            </Panel>
          </div>
          <Panel title="Experiments" actions={<Link to="/research">research lab</Link>} pad={false}>
            {d.experiments.length === 0 ? <Empty title="No experiments yet" /> : (
              <table className="t"><tbody>{d.experiments.map((e) => <tr key={e.id}><td><Link to={`/research/${e.id}`}>{e.name}</Link></td><td><StatusMark s={e.status} /></td><td>{e.progress}/{e.total}</td><td>{when(e.created_at)}</td></tr>)}</tbody></table>)}
          </Panel>
          <p className="sub">{d.counts.repositories} repositories · {d.counts.investigations} investigations · {d.counts.test_executions} recorded test executions</p>
        </>
      )}
    </>
  );
}
