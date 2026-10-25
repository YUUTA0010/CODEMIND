"""Indexing: incremental/full, change detection, cancellation safety, original-repo immutability, DB/FTS consistency."""
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.session import session_scope
from app.indexing.indexer import create_job, run_indexing_job
from app.models import Chunk, DependencyEdge, IndexingJob, Repository, SourceFile, Symbol
from tests.conftest import index_repo, tree_hashes, write_tree

BASE = {
    "pkg/__init__.py": "",
    "pkg/a.py": "from .b import helper\n\n\ndef run():\n    return helper()\n",
    "pkg/b.py": "def helper():\n    return 1\n",
    "tests/test_a.py": "from pkg.a import run\n\n\ndef test_run():\n    assert run() == 1\n",
}


def counts():
    with session_scope() as s:
        c = {"files": s.scalar(select(func.count()).select_from(SourceFile)), "symbols": s.scalar(select(func.count()).select_from(Symbol)),
             "chunks": s.scalar(select(func.count()).select_from(Chunk)), "edges": s.scalar(select(func.count()).select_from(DependencyEdge)),
             "fts": s.connection().exec_driver_sql("select count(*) from chunk_fts").scalar()}
    return c


def run_job(rid, mode="incremental"):
    with session_scope() as s:
        job = create_job(s, s.get(Repository, rid), mode)
        jid = job.id
    return run_indexing_job(jid)


def test_full_index_then_noop_incremental(db, tmp_path):
    repo = write_tree(tmp_path / "repo", BASE)
    rid = index_repo(str(repo))
    c1 = counts()
    assert c1["files"] == 4 and c1["fts"] == c1["chunks"] > 0
    r = run_job(rid)
    assert r["files_unchanged"] == 4 and r["files_modified"] == 0 and r["changed"] is False and r["index_version"] == 1
    assert counts() == c1


def test_incremental_detects_modify_add_delete(db, tmp_path):
    repo = write_tree(tmp_path / "repo", BASE)
    rid = index_repo(str(repo))
    (repo / "pkg/b.py").write_text("def helper():\n    return 2\n\n\ndef extra():\n    return helper()\n")
    (repo / "pkg/new.py").write_text("def n():\n    return 0\n")
    (repo / "tests/test_a.py").unlink()
    r = run_job(rid)
    assert (r["files_modified"], r["files_new"], r["files_deleted"]) == (1, 1, 1)
    assert r["index_version"] == 2
    with session_scope() as s:
        names = {q for (q,) in s.execute(select(Symbol.qualified_name))}
        paths = {p for (p,) in s.execute(select(SourceFile.path))}
    assert "extra" in names and "n" in names and "test_run" not in names
    assert "tests/test_a.py" not in paths
    c = counts()
    assert c["fts"] == c["chunks"], "FTS rows must stay in sync with chunks after incremental updates"


def test_edges_are_recomputed_when_a_dependency_changes(db, tmp_path):
    repo = write_tree(tmp_path / "repo", BASE)
    rid = index_repo(str(repo))

    def call_edges():
        with session_scope() as s:
            return s.scalar(select(func.count()).select_from(DependencyEdge).where(DependencyEdge.edge_type == "CALLS", DependencyEdge.resolution == "CONFIRMED"))

    before = call_edges()
    (repo / "pkg/b.py").write_text("def other():\n    return 1\n")  # helper removed -> a.run's call can no longer resolve
    run_job(rid)
    assert call_edges() < before


def test_full_reindex_rebuilds_everything(db, tmp_path):
    repo = write_tree(tmp_path / "repo", BASE)
    rid = index_repo(str(repo))
    c1 = counts()
    r = run_job(rid, "full")
    assert r["mode"] == "full" and r["files_reindexed"] == 4 and r["files_modified"] == 0 and r["files_new"] == 0 and r["index_version"] == 2
    c2 = counts()
    assert c2 == c1


def test_original_repository_is_never_modified(db, tmp_path):
    repo = write_tree(tmp_path / "repo", BASE)
    before = tree_hashes(repo)
    listing = sorted(str(q.relative_to(repo)) for q in repo.rglob("*"))
    mtimes = {p: p.stat().st_mtime_ns for p in repo.rglob("*") if p.is_file()}
    rid = index_repo(str(repo))
    run_job(rid)
    run_job(rid, "full")
    assert tree_hashes(repo) == before
    assert {p: p.stat().st_mtime_ns for p in repo.rglob("*") if p.is_file()} == mtimes
    assert sorted(str(q.relative_to(repo)) for q in repo.rglob("*")) == listing  # no files or directories were created
    assert not list(repo.rglob("*.cm_tmp")) and not (repo / ".codemind").exists()


def test_cancellation_leaves_index_unchanged(db, tmp_path):
    repo = write_tree(tmp_path / "repo", BASE)
    rid = index_repo(str(repo))
    c1 = counts()
    with session_scope() as s:
        repo_row = s.get(Repository, rid)
        job = create_job(s, repo_row, "full")
        job.cancel_requested = True  # cancellation requested before/while running
        jid = job.id
    (repo / "pkg/b.py").write_text("def changed():\n    return 9\n")
    res = run_indexing_job(jid)
    assert res["status"] == "CANCELLED"
    assert counts() == c1, "a cancelled job must not leave a half-written index"
    with session_scope() as s:
        r = s.get(Repository, rid)
        assert r.status == "INDEXED" and r.index_version == 1
        assert s.get(IndexingJob, jid).status == "CANCELLED"
        assert "helper" in {q for (q,) in s.execute(select(Symbol.qualified_name))}


def test_parse_errors_are_recorded_and_do_not_abort(db, tmp_path):
    repo = write_tree(tmp_path / "repo", {"good.py": "def ok():\n    return 1\n", "bad.py": "def (:\n"})
    index_repo(str(repo))
    with session_scope() as s:
        bad = s.execute(select(SourceFile).where(SourceFile.path == "bad.py")).scalar_one()
        assert "SyntaxError" in bad.parse_error
        assert s.scalar(select(func.count()).select_from(Symbol).where(Symbol.name == "ok")) == 1


def test_embedding_model_change_forces_full_reindex(db, tmp_path, monkeypatch):
    repo = write_tree(tmp_path / "repo", BASE)
    rid = index_repo(str(repo))
    with session_scope() as s:
        s.query(Chunk).update({Chunk.embedding_model: "some-other-model"})
    r = run_job(rid, "incremental")
    assert r["forced_full_reindex"] is True and r["mode"] == "full"


def test_database_constraints_and_cascades(db, tmp_path):
    repo = write_tree(tmp_path / "repo", BASE)
    rid = index_repo(str(repo))
    with pytest.raises(IntegrityError):
        with session_scope() as s:
            s.add(Symbol(repository_id=rid, file_id=99999, kind="function", name="x", qualified_name="x", start_line=1, end_line=1))
    with pytest.raises(IntegrityError):  # unique (repository_id, path)
        with session_scope() as s:
            s.add(SourceFile(repository_id=rid, path="pkg/a.py"))
    with pytest.raises(IntegrityError):  # unique root_path
        with session_scope() as s:
            s.add(Repository(name="dup", root_path=str(repo.resolve())))


def test_deleting_repository_cascades_via_orm(db, tmp_path):
    repo = write_tree(tmp_path / "repo", BASE)
    rid = index_repo(str(repo))
    with session_scope() as s:
        s.delete(s.get(Repository, rid))
    c = counts()
    assert (c["files"], c["symbols"], c["chunks"], c["edges"]) == (0, 0, 0, 0)
