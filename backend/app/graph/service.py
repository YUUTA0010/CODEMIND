"""Dependency graph queries (NetworkX) over the persisted edges.

Node ids: "r<ID>" repository, "f<ID>" file, "s<ID>" symbol. Unresolved edges have no concrete target and are therefore
kept out of the graph by default (listed separately via `unresolved_for`), so traversals never follow guessed links.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models import DependencyEdge, Repository, SourceFile, Symbol

EDGE_TYPES = ["IMPORTS", "DEFINES", "CALLS", "INHERITS", "REFERENCES", "TESTS", "DEPENDS_ON"]
TRAVERSAL_DEFAULT = ["CALLS", "INHERITS", "REFERENCES", "TESTS", "IMPORTS"]


@dataclass
class RepoGraph:
    repo_id: int
    index_version: int
    fingerprint: str = ""
    g: nx.MultiDiGraph = field(default_factory=nx.MultiDiGraph)
    info: dict[str, dict] = field(default_factory=dict)
    unresolved: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ construction
    @classmethod
    def build(cls, s: Session, repo_id: int) -> "RepoGraph":
        repo = s.get(Repository, repo_id)
        if repo is None:
            raise NotFoundError(f"repository {repo_id} not found")
        rg = cls(repo_id, repo.index_version, repo.fingerprint)
        rid = f"r{repo.id}"
        rg.g.add_node(rid)
        rg.info[rid] = {"id": rid, "kind": "repository", "label": repo.name, "path": "", "language": None}
        files = s.execute(select(SourceFile).where(SourceFile.repository_id == repo_id)).scalars().all()
        for f in files:
            nid = f"f{f.id}"
            rg.g.add_node(nid)
            rg.info[nid] = {"id": nid, "kind": "test_file" if f.is_test else ("config" if f.language == "config" else "file"),
                            "label": f.path.rsplit("/", 1)[-1], "path": f.path, "language": f.language,
                            "start_line": 1, "end_line": f.line_count, "file_id": f.id}
            rg.g.add_edge(rid, nid, key="STRUCT", type="DEFINES", resolution="CONFIRMED", confidence=1.0, provenance="repository contains file")
        path_by_id = {f.id: f.path for f in files}
        lang_by_id = {f.id: f.language for f in files}
        for sym in s.execute(select(Symbol).where(Symbol.repository_id == repo_id)).scalars():
            nid = f"s{sym.id}"
            rg.g.add_node(nid)
            rg.info[nid] = {"id": nid, "kind": "test" if sym.is_test and sym.kind != "module" else sym.kind, "label": sym.qualified_name if sym.kind != "module" else sym.name,
                            "path": path_by_id.get(sym.file_id, ""), "language": lang_by_id.get(sym.file_id), "start_line": sym.start_line,
                            "end_line": sym.end_line, "signature": sym.signature, "file_id": sym.file_id, "symbol_id": sym.id}
        for e in s.execute(select(DependencyEdge).where(DependencyEdge.repository_id == repo_id)).scalars():
            src = f"s{e.src_symbol_id}" if e.src_symbol_id else (f"f{e.src_file_id}" if e.src_file_id else None)
            dst = f"s{e.dst_symbol_id}" if e.dst_symbol_id else (f"f{e.dst_file_id}" if e.dst_file_id else None)
            if src not in rg.info:
                continue
            attrs = {"type": e.edge_type, "resolution": e.resolution, "confidence": e.confidence, "provenance": e.provenance, "line": e.line}
            if e.resolution == "UNRESOLVED" or dst is None or dst not in rg.info:
                rg.unresolved.append({"src": src, "type": e.edge_type, "target": e.unresolved_target, "provenance": e.provenance, "line": e.line})
                continue
            rg.g.add_edge(src, dst, key=f"{e.edge_type}:{e.id}", **attrs)
        return rg

    # ------------------------------------------------------------------ queries
    def _check(self, node: str) -> None:
        if node not in self.info:
            raise NotFoundError(f"graph node {node} not found")

    def _edges(self, node: str, direction: str, types: list[str] | None, min_conf: float):
        out = []
        it = self.g.out_edges(node, data=True) if direction == "out" else self.g.in_edges(node, data=True)
        for u, v, d in it:
            if types and d["type"] not in types:
                continue
            if d["confidence"] < min_conf:
                continue
            out.append((u, v, d))
        return out

    def direct_dependencies(self, node: str, types: list[str] | None = None, min_conf: float = 0.0) -> list[dict]:
        self._check(node)
        return [self._edge_view(u, v, d, v) for u, v, d in self._edges(node, "out", types, min_conf)]

    def reverse_dependencies(self, node: str, types: list[str] | None = None, min_conf: float = 0.0) -> list[dict]:
        self._check(node)
        return [self._edge_view(u, v, d, u) for u, v, d in self._edges(node, "in", types, min_conf)]

    def callers(self, node: str, min_conf: float = 0.0) -> list[dict]:
        return self.reverse_dependencies(node, ["CALLS"], min_conf)

    def callees(self, node: str, min_conf: float = 0.0) -> list[dict]:
        return self.direct_dependencies(node, ["CALLS"], min_conf)

    def _edge_view(self, u, v, d, other) -> dict:
        return {"node": self.info[other], "edge_type": d["type"], "resolution": d["resolution"], "confidence": d["confidence"],
                "provenance": d["provenance"], "line": d.get("line")}

    def neighborhood(self, node: str, depth: int = 2, types: list[str] | None = None, min_conf: float = 0.0,
                     direction: str = "both", max_nodes: int = 200) -> dict:
        """Bounded BFS. Returns node ids with hop distance; `truncated` is set when the cap cuts the result."""
        self._check(node)
        depth = max(0, min(depth, 6))
        types = types or TRAVERSAL_DEFAULT
        dist = {node: 0}
        frontier = [node]
        truncated = False
        for hop in range(1, depth + 1):
            nxt = []
            for n in frontier:
                nbrs = []
                if direction in ("out", "both"):
                    nbrs += [v for _, v, _ in self._edges(n, "out", types, min_conf)]
                if direction in ("in", "both"):
                    nbrs += [u for u, _, _ in self._edges(n, "in", types, min_conf)]
                for m in nbrs:
                    if m not in dist:
                        if len(dist) >= max_nodes:
                            truncated = True
                            continue
                        dist[m] = hop
                        nxt.append(m)
            frontier = nxt
        return {"center": node, "distances": dist, "truncated": truncated, "max_nodes": max_nodes}

    def transitive_reverse(self, node: str, types: list[str] | None = None, min_conf: float = 0.5, depth: int = 5) -> dict[str, int]:
        """Everything that (transitively) depends on `node` — the blast radius of a change."""
        return self.neighborhood(node, depth, types or ["CALLS", "INHERITS", "REFERENCES", "IMPORTS"], min_conf, "in", 1000)["distances"]

    def shortest_path(self, a: str, b: str, types: list[str] | None = None, min_conf: float = 0.0) -> list[str] | None:
        self._check(a)
        self._check(b)
        types = types or TRAVERSAL_DEFAULT
        view = nx.DiGraph()
        for u, v, d in self.g.edges(data=True):
            if d["type"] in types and d["confidence"] >= min_conf:
                view.add_edge(u, v)
        try:
            return nx.shortest_path(view.to_undirected(as_view=True), a, b)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def file_graph(self, types: list[str] | None = None, min_conf: float = 0.0) -> nx.DiGraph:
        """Collapse symbol edges onto files (for components and centrality)."""
        types = types or ["IMPORTS", "DEPENDS_ON", "CALLS", "INHERITS"]
        fg = nx.DiGraph()
        for n, inf in self.info.items():
            if inf["kind"] in ("file", "test_file", "config"):
                fg.add_node(n)
        for u, v, d in self.g.edges(data=True):
            if d["type"] not in types or d["confidence"] < min_conf:
                continue
            fu = u if u.startswith("f") else f"f{self.info[u]['file_id']}" if u in self.info and "file_id" in self.info[u] else None
            fv = v if v.startswith("f") else f"f{self.info[v]['file_id']}" if v in self.info and "file_id" in self.info[v] else None
            if fu and fv and fu != fv and fu in fg and fv in fg:
                fg.add_edge(fu, fv)
        return fg

    def components(self) -> list[dict]:
        fg = self.file_graph()
        comps = sorted(nx.weakly_connected_components(fg), key=len, reverse=True)
        return [{"size": len(c), "files": sorted(self.info[n]["path"] for n in c)} for c in comps]

    def degree_centrality(self, top: int = 20) -> list[dict]:
        fg = self.file_graph()
        cent = nx.degree_centrality(fg) if fg.number_of_nodes() > 1 else {}
        ranked = sorted(cent.items(), key=lambda kv: kv[1], reverse=True)[:top]
        return [{"path": self.info[n]["path"], "centrality": round(c, 4), "in_degree": fg.in_degree(n), "out_degree": fg.out_degree(n)} for n, c in ranked]

    def unresolved_for(self, node: str) -> list[dict]:
        return [u for u in self.unresolved if u["src"] == node]

    # ------------------------------------------------------------------ export
    def export(self, *, level: str = "file", languages: list[str] | None = None, directory: str | None = None,
               edge_types: list[str] | None = None, min_confidence: float = 0.0, include_tests: bool = True,
               center: str | None = None, depth: int = 2, max_nodes: int = 400) -> dict:
        """Cytoscape-ready elements. `level` is "file" or "symbol". Reports truncation explicitly."""
        edge_types = edge_types or (["IMPORTS", "TESTS", "DEPENDS_ON"] if level == "file" else ["CALLS", "INHERITS", "REFERENCES", "TESTS", "DEFINES"])
        allowed_kinds = {"file", "test_file", "config"} if level == "file" else {"file", "test_file", "class", "function", "method", "test", "interface", "type"}
        if level == "symbol" and "DEFINES" not in edge_types:
            allowed_kinds.discard("file")
        keep: set[str] = set()
        scope = None
        if center:
            scope = set(self.neighborhood(center, depth, edge_types + ["DEFINES"] if level == "symbol" else edge_types, min_confidence, "both", max_nodes * 2)["distances"])
        for n, inf in self.info.items():
            if inf["kind"] not in allowed_kinds:  # module pseudo-symbols and the repository node never appear in views
                continue
            if scope is not None and n not in scope:
                continue
            if languages and inf.get("language") not in languages:
                continue
            if directory and not (inf.get("path", "").startswith(directory.rstrip("/") + "/") or inf.get("path") == directory):
                continue
            if not include_tests and inf["kind"] in ("test_file", "test"):
                continue
            keep.add(n)
        truncated = len(keep) > max_nodes
        if truncated:
            deg = {n: self.g.degree(n) for n in keep}
            keep = set(sorted(keep, key=lambda n: -deg[n])[:max_nodes])
        edges = []
        for u, v, k, d in self.g.edges(keys=True, data=True):
            if u in keep and v in keep and d["type"] in edge_types and d["confidence"] >= min_confidence:
                edges.append({"data": {"id": f"{u}-{v}-{k}", "source": u, "target": v, "type": d["type"], "resolution": d["resolution"],
                                       "confidence": round(d["confidence"], 3), "provenance": d["provenance"]}})
        nodes = [{"data": {**{k: v for k, v in self.info[n].items()}}} for n in keep]
        return {"elements": {"nodes": nodes, "edges": edges}, "truncated": truncated, "total_matching_nodes": len(keep) if not truncated else None,
                "node_count": len(nodes), "edge_count": len(edges), "level": level, "unresolved_edge_count": len(self.unresolved)}


_cache: dict[int, RepoGraph] = {}
_lock = threading.Lock()


def get_graph(s: Session, repo_id: int) -> RepoGraph:
    repo = s.get(Repository, repo_id)
    if repo is None:
        raise NotFoundError(f"repository {repo_id} not found")
    with _lock:
        cached = _cache.get(repo_id)
        if cached is not None and cached.index_version == repo.index_version and cached.fingerprint == repo.fingerprint:
            return cached
    rg = RepoGraph.build(s, repo_id)
    with _lock:
        _cache[repo_id] = rg
    return rg


def invalidate(repo_id: int | None = None) -> None:
    with _lock:
        if repo_id is None:
            _cache.clear()
        else:
            _cache.pop(repo_id, None)
