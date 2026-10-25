"""Dependency graph: edge construction, honesty about ambiguity, traversal and analysis queries."""
import pytest
from sqlalchemy import select

from app.core.errors import NotFoundError
from app.db.session import session_scope
from app.graph.service import RepoGraph, get_graph
from app.models import DependencyEdge, SourceFile, Symbol
from tests.conftest import index_repo, write_tree

PROJECT = {
    "app/__init__.py": "",
    "app/base.py": "class Animal:\n    def speak(self):\n        return 'x'\n\n    def name(self):\n        return 'animal'\n",
    "app/dog.py": "from .base import Animal\nfrom . import util\nfrom .util import shout\n\n\nclass Dog(Animal):\n    def speak(self):\n        return shout(self.name())\n\n    def fetch(self):\n        return util.tool()\n",
    "app/util.py": "def shout(s):\n    return s.upper()\n\n\ndef tool():\n    return shout('x')\n",
    "app/dyn.py": "import requests\nimport os\n\n\ndef go(obj, handlers):\n    requests.get('u')\n    obj.process()\n    handlers['a']().run()\n    missing_func()\n    return os.path.join('a')\n",
    # two unrelated classes define `process` => a name-only call must NOT become a confident edge
    "app/p1.py": "class P1:\n    def process(self):\n        return 1\n",
    "app/p2.py": "class P2:\n    def process(self):\n        return 2\n",
    "tests/test_dog.py": "from app.dog import Dog\n\n\ndef test_speak():\n    assert Dog().speak() == 'ANIMAL'\n",
}


@pytest.fixture()
def graph(db, tmp_path):
    repo = write_tree(tmp_path / "proj", PROJECT)
    rid = index_repo(str(repo))
    with session_scope() as s:
        g = get_graph(s, rid)
        ids = {q: sid for sid, q in s.execute(select(Symbol.id, Symbol.qualified_name)).all()}
        fids = {p: fid for fid, p in s.execute(select(SourceFile.id, SourceFile.path)).all()}
        edges = s.execute(select(DependencyEdge)).scalars().all()
        for e in edges:
            s.expunge(e)
    return rid, g, ids, fids, edges


def find(edges, type_, *, src=None, dst=None, res=None):
    return [e for e in edges if e.edge_type == type_ and (src is None or e.src_symbol_id == src) and (dst is None or e.dst_symbol_id == dst)
            and (res is None or e.resolution == res)]


def test_import_and_call_edges_are_confirmed(graph):
    rid, g, ids, fids, edges = graph
    assert find(edges, "CALLS", src=ids["Dog.speak"], dst=ids["shout"], res="CONFIRMED")  # via `from .util import shout`
    assert find(edges, "CALLS", src=ids["Dog.fetch"], dst=ids["tool"], res="CONFIRMED")  # via `from . import util` module alias
    assert find(edges, "CALLS", src=ids["tool"], dst=ids["shout"], res="CONFIRMED")  # same-package lexical import
    imp = [e for e in edges if e.edge_type == "IMPORTS" and e.src_file_id == fids["app/dog.py"] and e.dst_file_id == fids["app/base.py"]]
    assert imp and imp[0].resolution == "CONFIRMED"


def test_self_method_and_inheritance(graph):
    rid, g, ids, fids, edges = graph
    # self.name() is defined on the BASE class: resolved through the hierarchy, flagged INFERRED (not direct)
    c = find(edges, "CALLS", src=ids["Dog.speak"], dst=ids["Animal.name"])
    assert c and c[0].resolution in ("CONFIRMED", "INFERRED") and c[0].confidence >= 0.8
    inh = find(edges, "INHERITS", src=None)
    assert any(e.dst_symbol_id == ids["Animal"] for e in inh)


def test_ambiguous_name_only_calls_are_never_confident_edges(graph):
    rid, g, ids, fids, edges = graph
    # obj.process() has two candidates (P1.process, P2.process): must be UNRESOLVED with NO target
    amb = [e for e in edges if e.edge_type == "CALLS" and e.src_symbol_id == ids["go"] and e.unresolved_target and e.unresolved_target.endswith("process")]
    assert amb and all(e.resolution == "UNRESOLVED" and e.dst_symbol_id is None and e.confidence == 0.0 for e in amb)
    assert not find(edges, "CALLS", src=ids["go"], dst=ids["P1.process"]) and not find(edges, "CALLS", src=ids["go"], dst=ids["P2.process"])


def test_external_and_dynamic_calls_are_unresolved_and_kept_visible(graph):
    rid, g, ids, fids, edges = graph
    unresolved = {e.unresolved_target for e in edges if e.resolution == "UNRESOLVED" and e.src_symbol_id == ids["go"]}
    assert "requests.get" in unresolved and "missing_func" in unresolved and "<dynamic>.run" in unresolved
    unresolved_imports = {e.unresolved_target for e in edges if e.edge_type == "IMPORTS" and e.resolution == "UNRESOLVED"}
    assert {"requests", "os"} <= unresolved_imports
    # unresolved edges are listed separately and are NOT traversable graph edges
    assert any(u["target"] == "requests.get" for u in g.unresolved_for(f"s{ids['go']}"))
    assert all(d["resolution"] != "UNRESOLVED" for _, _, d in g.g.edges(data=True))


def test_test_edges(graph):
    rid, g, ids, fids, edges = graph
    t = find(edges, "TESTS", src=ids["test_speak"])
    assert any(e.dst_symbol_id == ids["Dog"] for e in t) and all(e.resolution == "INFERRED" for e in t)
    assert [e for e in edges if e.edge_type == "TESTS" and e.src_file_id == fids["tests/test_dog.py"] and e.dst_file_id == fids["app/dog.py"]]


def test_callers_callees_and_reverse_dependencies(graph):
    rid, g, ids, fids, edges = graph
    callers = {c["node"]["label"] for c in g.callers(f"s{ids['shout']}")}
    assert {"Dog.speak", "tool"} <= callers
    callees = {c["node"]["label"] for c in g.callees(f"s{ids['Dog.speak']}")}
    assert "shout" in callees
    blast = g.transitive_reverse(f"s{ids['shout']}")
    assert f"s{ids['Dog.fetch']}" in blast and blast[f"s{ids['tool']}"] == 1


def test_neighborhood_is_bounded_and_reports_truncation(graph):
    rid, g, ids, fids, edges = graph
    n = g.neighborhood(f"s{ids['shout']}", depth=1)
    assert max(n["distances"].values()) == 1
    capped = g.neighborhood(f"s{ids['shout']}", depth=3, max_nodes=2)
    assert len(capped["distances"]) <= 2 and capped["truncated"] is True
    with pytest.raises(NotFoundError):
        g.neighborhood("s999999")


def test_shortest_path_components_centrality(graph):
    rid, g, ids, fids, edges = graph
    path = g.shortest_path(f"s{ids['Dog.fetch']}", f"s{ids['shout']}")
    assert path and path[0] == f"s{ids['Dog.fetch']}" and path[-1] == f"s{ids['shout']}"
    assert g.shortest_path(f"s{ids['P1.process']}", f"s{ids['shout']}") is None  # disconnected: no invented path
    comps = g.components()
    assert any({"app/dog.py", "app/util.py", "app/base.py"} <= set(c["files"]) for c in comps)
    assert any(c["files"] == ["app/p1.py"] for c in comps)
    cent = {c["path"]: c for c in g.degree_centrality(50)}
    assert cent["app/util.py"]["centrality"] >= cent["app/p2.py"]["centrality"]


def test_export_filters(graph):
    rid, g, ids, fids, edges = graph
    full = g.export(level="file")
    labels = {n["data"]["path"] for n in full["elements"]["nodes"]}
    assert "app/dog.py" in labels and full["truncated"] is False
    only_app = g.export(level="file", directory="app")
    assert all(n["data"]["path"].startswith("app/") for n in only_app["elements"]["nodes"])
    no_tests = g.export(level="file", include_tests=False)
    assert all(not n["data"]["path"].startswith("tests/") for n in no_tests["elements"]["nodes"])
    sym = g.export(level="symbol", edge_types=["CALLS"], min_confidence=0.9)
    assert all(e["data"]["type"] == "CALLS" and e["data"]["confidence"] >= 0.9 for e in sym["elements"]["edges"])
    small = g.export(level="symbol", max_nodes=3)
    assert small["truncated"] is True and small["node_count"] == 3
    centered = g.export(level="symbol", center=f"s{ids['shout']}", depth=1)
    assert f"s{ids['shout']}" in {n["data"]["id"] for n in centered["elements"]["nodes"]}
    assert g.export(level="symbol", languages=["javascript"])["node_count"] == 0


def test_graph_cache_follows_index_version(db, tmp_path):
    repo = write_tree(tmp_path / "p", {"a.py": "def f():\n    return 1\n"})
    rid = index_repo(str(repo))
    with session_scope() as s:
        g1 = get_graph(s, rid)
        assert get_graph(s, rid) is g1
    (repo / "a.py").write_text("def f():\n    return 1\n\n\ndef g():\n    return f()\n")
    index_repo(str(repo), mode="incremental")
    with session_scope() as s:
        g2 = get_graph(s, rid)
        assert g2 is not g1 and any(i.get("label") == "g" for i in g2.info.values())
