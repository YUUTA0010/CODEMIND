"""End to end: index -> bug report -> retrieval -> localization -> AI analysis -> patch -> sandbox verification -> report -> export.

Uses a SYNTHETIC fixture repository with a known injected bug and a scripted model TEST DOUBLE (no paid API required).
"""
import json
from pathlib import Path

import pytest

from app.agents.providers import ScriptedProvider
from app.core.events import bus
from tests.conftest import tree_hashes

pytestmark = pytest.mark.slow


def test_full_workflow_over_http(client, monkeypatch):
    # 1-3. register + index a repository (demo mode = clearly labelled synthetic repository)
    demo = client.post("/api/v1/demo/load").json()
    assert demo["demo"] is True and "SYNTHETIC" in demo["notice"]
    rid, task = demo["repository"]["id"], demo["task"]
    repo_path = Path(demo["repository"]["root_path"])
    original = tree_hashes(repo_path)
    st = client.get(f"/api/v1/repositories/{rid}/index-status").json()
    assert st["job"]["status"] == "COMPLETED" and st["index_version"] == 1 and st["job"]["stats"]["parse_errors"] == 0
    repo = client.get(f"/api/v1/repositories/{rid}").json()
    assert repo["kind"] == "demo" and repo["language_stats"]["python"]["files"] >= 8 and repo["file_count"] == 18

    # 4-6. inspect structure, symbols, graph, search
    assert any(f["path"] == "inventory/pricing.py" for f in client.get(f"/api/v1/repositories/{rid}/files").json()["files"])
    syms = client.get(f"/api/v1/repositories/{rid}/symbols", params={"q": "apply_discount", "kind": "function"}).json()["items"]
    target = next(s for s in syms if s["name"] == "apply_discount")
    assert target["path"] == "inventory/pricing.py" and target["start_line"] < target["end_line"]
    node = client.get(f"/api/v1/repositories/{rid}/graph/node/{target['node_id']}").json()
    assert {"Cart.subtotal"} <= {c["node"]["label"] for c in node["callers"]} and any(t["node"]["label"].endswith("test_discount_applies_at_threshold") for t in node["dependents"])
    hits = client.post("/api/v1/search", json={"repository_id": rid, "query": "bulk discount threshold", "mode": "hybrid"}).json()
    assert hits["results"][0]["path"] == "inventory/pricing.py" and hits["results"][0]["snippet"] and hits["results"][0]["explanation"]

    # 7-10. investigation: retrieval + ranked candidates + evidence
    inv = client.post("/api/v1/investigations", json={"repository_id": rid, "title": "bulk discount off by one", "description": task["description"],
                                                     "failing_tests": task["relevant_tests"]}).json()
    assert inv["bug_report"]["extracted"]["failing_tests"] == task["relevant_tests"]
    ret = client.post(f"/api/v1/investigations/{inv['id']}/retrieve", json={"options": {"method": "hybrid_dependency"}}).json()
    top = ret["candidates"][0]
    assert top["file_path"] == "inventory/pricing.py" and top["symbol_name"] == "apply_discount" and top["evidence"] and top["uncertainty"] and top["related_tests"]
    assert ret["retrieval_run"]["config"]["method"] == "hybrid_dependency" and ret["retrieval_run"]["index_version"] == 1 and ret["context"]["snippets"]
    assert "not a proven root cause" in " ".join(ret["candidate_uncertainty"]["1"])

    # no model configured: deterministic and honest
    plain = client.post(f"/api/v1/investigations/{inv['id']}/analyze", json={"use_model": True}).json()
    assert plain["analysis"]["origin"] == "deterministic" and plain["analysis"]["proposed_patch"] is None

    # AI analysis with a model double: one real citation, one fabricated
    diff = next(r for r in __import__("app.experiments.adapters.synthetic", fromlist=["x"]).materialize_suite(Path(repo_path).parents[1] / "tmp_suite") if r["task_key"] == task["task_key"])["oracle_fix_diff"]
    answer = {"action": "final", "summary": "The threshold comparison excludes the boundary quantity.",
              "hypotheses": [{"statement": "apply_discount uses > instead of >=", "supporting": ["docstring says 'or more'"], "contradicting": [],
                              "citations": [{"path": "inventory/pricing.py", "start_line": target["start_line"], "end_line": target["end_line"]},
                                            {"path": "inventory/ghost.py", "start_line": 1, "end_line": 3}]}],
              "patch": {"diff": diff, "rationale": "use >= so the boundary quantity gets the discount", "behavior_before": "10 units: no discount", "behavior_after": "10 units: 10% off"},
              "affected_tests": task["relevant_tests"], "unverified": ["only the listed tests were considered"]}
    prov = ScriptedProvider([json.dumps(answer)])
    monkeypatch.setattr("app.api.investigations.get_provider", lambda: prov)
    ana = client.post(f"/api/v1/investigations/{inv['id']}/analyze", json={"use_model": True}).json()
    assert ana["analysis"]["origin"] == "model" and ana["analysis"]["provider_available"] is True
    h = ana["hypotheses"][0]
    assert h["origin"] == "model" and len(h["citations"]) == 1 and len(h["rejected_citations"]) == 1 and h["rejected_citations"][0]["path"] == "inventory/ghost.py"
    assert ana["candidates"][0]["file_path"] == "inventory/pricing.py", "algorithmic candidates are stored separately from model text"
    assert ana["status"] == "ANALYZED"

    # 11-14. patch -> review -> sandbox verification
    created = client.post(f"/api/v1/investigations/{inv['id']}/patches", json={"from_analysis": True})
    assert created.status_code == 201
    patch = created.json()
    assert patch["status"] == "PATCH_GENERATED" and patch["changed_files"] == ["inventory/pricing.py"] and patch["model_name"] == "scripted"
    assert "quantity >= BULK_THRESHOLD" in patch["diff"] and tree_hashes(repo_path) == original, "nothing applied to the real tree yet"
    seen = []
    with client.websocket_connect("/api/v1/ws/events") as ws:
        assert ws.receive_json()["type"] == "hello"
        rep = client.post(f"/api/v1/patches/{patch['id']}/verify", json={}).json()
        bus.publish("probe", {})
        while True:
            ev = ws.receive_json()
            seen.append(ev["type"])
            if ev["type"] == "probe":
                break
    assert {"verify.progress", "verify.done"} <= set(seen), seen
    assert rep["status"] == "TESTS_PASSED"
    ph = {e["phase"]: e for e in rep["executions"]}
    assert ph["baseline"]["outcome"] == "TESTS_FAILED" and ph["targeted"]["outcome"] == "TESTS_PASSED" and ph["broad"]["tests_passed"] == 20
    assert rep["sandbox_modes"] == ["local"] and rep["verification_limits"] and rep["possible_regression"] is False
    assert tree_hashes(repo_path) == original

    # 15. explicit approval is required; applying to the working tree is a separate explicit step
    assert client.post(f"/api/v1/patches/{patch['id']}/export", json={}).status_code == 403
    exp = client.post(f"/api/v1/patches/{patch['id']}/export", json={"approve": True}).json()
    assert Path(exp["exported_to"]).is_file() and exp["verification_status"] == "TESTS_PASSED" and tree_hashes(repo_path) == original
    done = client.post(f"/api/v1/patches/{patch['id']}/export", json={"approve": True, "apply_to_working_tree": True}).json()
    assert done["applied_files"] == ["inventory/pricing.py"] and "quantity >= BULK_THRESHOLD" in (repo_path / "inventory/pricing.py").read_text()

    # 16. everything is stored for later review
    full = client.get(f"/api/v1/investigations/{inv['id']}").json()
    assert full["patches"][0]["id"] == patch["id"] and full["hypotheses"] and full["candidates"] and full["retrieval_run"]
    got = client.get(f"/api/v1/patches/{patch['id']}").json()
    assert got["report"]["status"] == "TESTS_PASSED" and got["export_approved"] is True
    dash = client.get("/api/v1/dashboard").json()
    assert dash["recent_verifications"][0]["status"] == "TESTS_PASSED" and dash["counts"]["test_executions"] == 3
    actions = [a["action"] for a in client.get("/api/v1/audit", params={"limit": 100}).json()["items"]]
    assert {"investigation.created", "investigation.analyzed", "patch.created", "patch.verified", "patch.exported"} <= set(actions)


def test_indexing_events_stream_and_conflicts(client, env):
    from tests.conftest import write_tree

    d = write_tree(env / "p", {"a.py": "def f():\n    return 1\n"})
    rid = client.post("/api/v1/repositories", json={"path": str(d)}).json()["id"]
    assert client.get(f"/api/v1/repositories/{rid}/graph").status_code == 409  # not indexed yet: clear error, not an empty graph
    assert client.post("/api/v1/search", json={"repository_id": rid, "query": "f"}).json()["error"]["code"] == "not_indexed"
    with client.websocket_connect("/api/v1/ws/events") as ws:
        ws.receive_json()
        job = client.post(f"/api/v1/repositories/{rid}/index", json={"mode": "full"})
        assert job.status_code == 202
        types = []
        bus.publish("probe", {})
        while True:
            ev = ws.receive_json()
            types.append(ev["type"])
            if ev["type"] == "probe":
                break
    assert "index.started" in types and "index.done" in types and "index.progress" in types
    st = client.get(f"/api/v1/repositories/{rid}/index-status").json()
    assert st["job"]["status"] == "COMPLETED" and st["job"]["progress"] == st["job"]["total"]
    assert client.post(f"/api/v1/repositories/{rid}/index/cancel").status_code == 404  # nothing running
    assert client.get(f"/api/v1/repositories/{rid}/graph", params={"edge_type": "BOGUS"}).status_code == 422
    assert client.get(f"/api/v1/repositories/{rid}/graph/node/s99999").status_code == 404
