import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";
import type { Page, Repository, SearchResponse } from "../types/api";
import { Banner, Empty, ErrorBanner, EpistemicLegend, Mark, PageHead, Panel, fmt } from "../components/ui";

const MODES = [["hybrid", "Hybrid (lexical + semantic + symbol)"], ["lexical", "Lexical (BM25)"], ["semantic", "Semantic"], ["symbol", "Symbol name"], ["dependency", "Dependency-aware"]];

export default function SearchPage() {
  const repos = useApi<Page<Repository>>("/repositories?limit=100");
  const [sp, setSp] = useSearchParams();
  const [rid, setRid] = useState(sp.get("repo") ?? "");
  const [query, setQuery] = useState(sp.get("q") ?? "");
  const [mode, setMode] = useState("hybrid");
  const [res, setRes] = useState<SearchResponse | null>(null);
  const indexed = (repos.data?.items ?? []).filter((r) => r.index_version > 0);
  const repoId = rid || (indexed[0] ? String(indexed[0].id) : "");
  const run = useAction(async () => { setSp({ repo: repoId, q: query }); setRes(await api.post<SearchResponse>("/search", { repository_id: Number(repoId), query, mode, limit: 20 })); });
  return (
    <>
      <PageHead title="Search" sub="Ranked code locations with the signals behind each score. Scores are heuristic ranking aids, not probabilities." />
      <Panel>
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (repoId && query.trim()) run.run(); }}>
          <select value={repoId} onChange={(e) => setRid(e.target.value)} aria-label="Repository">{indexed.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
          <input style={{ flex: 1, minWidth: 260 }} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Describe the behavior, error or symbol you are looking for" aria-label="Query" />
          <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Mode">{MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          <button className="primary" disabled={run.pending || !repoId || !query.trim()}>{run.pending ? "Searching…" : "Search"}</button>
        </form>
        {repos.data && indexed.length === 0 && <p className="sub" style={{ marginTop: 8 }}>No indexed repository. <Link to="/repositories">Index one first.</Link></p>}
      </Panel>
      <ErrorBanner error={run.error} />
      {res && (
        <>
          <EpistemicLegend />
          {res.warnings.map((w) => <Banner key={w} kind="warn">{w}</Banner>)}
          <p className="sub" style={{ marginBottom: 8 }}><Mark kind="fact">fact</Mark> method {res.method} · index v{res.index_version} · {fmt(res.runtime_ms, 1)} ms · signals: {res.active_signals.join(", ") || "none"}</p>
          {res.results.length === 0 ? <Panel><Empty title="No results">Try different words, or another mode.</Empty></Panel> : res.results.map((h) => (
            <Panel key={h.rank} title={<><span>#{h.rank}</span><Link className="mono" to={`/repositories/${repoId}`}>{h.path}:{h.start_line}-{h.end_line}</Link>{h.symbol && <Mark kind="neutral">{h.symbol}</Mark>}</>}
              actions={<Mark kind="heuristic">score {fmt(h.score)}</Mark>}>
              <div className="row" style={{ marginBottom: 6 }}>{Object.entries(h.signals).filter(([, v]) => v > 0).map(([k, v]) => <Mark key={k} kind="heuristic">{k} {fmt(v, 2)}</Mark>)}</div>
              {h.explanation.length > 0 && <ul className="plain">{h.explanation.map((x, i) => <li key={i}>{x}</li>)}</ul>}
              {h.snippet && <pre className="code" style={{ marginTop: 8 }}>{h.snippet}</pre>}
            </Panel>))}
        </>)}
    </>
  );
}
