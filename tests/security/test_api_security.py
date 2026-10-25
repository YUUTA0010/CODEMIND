"""API validation, authentication/authorization, traversal and secret-handling."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import session_scope
from app.models import AuditEvent, User


def demo(client):
    r = client.post("/api/v1/demo/load")
    assert r.status_code == 201
    return r.json()["repository"]["id"], r.json()["task"]


def test_error_shape_and_validation(client):
    r = client.post("/api/v1/repositories", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error" and r.json()["error"]["details"]
    assert client.get("/api/v1/repositories/999").json()["error"]["code"] == "not_found"
    assert client.get("/api/v1/repositories/abc").status_code == 422
    assert client.get("/api/v1/repositories?limit=0").status_code == 422
    assert client.get("/api/v1/repositories?limit=100000").status_code == 422
    assert client.post("/api/v1/search", json={"repository_id": 1, "query": "x", "mode": "bogus"}).status_code == 422
    assert client.post("/api/v1/search", json={"repository_id": 1, "query": "x", "disabled_signals": ["nope"]}).status_code == 422
    assert client.get("/api/v1/nonexistent").status_code == 404


def test_repository_registration_rules(client, env):
    d = env / "proj"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n")
    r = client.post("/api/v1/repositories", json={"path": str(d)})
    assert r.status_code == 201 and r.json()["status"] == "REGISTERED" and r.json()["kind"] == "user"
    assert client.post("/api/v1/repositories", json={"path": str(d)}).status_code == 409
    for bad in ["/nonexistent/path", "/", "/etc", str(d / "a.py")]:
        r = client.post("/api/v1/repositories", json={"path": bad})
        assert r.status_code in (400, 409) and r.json()["error"]["code"] in ("invalid_path", "conflict")


def test_pagination_shape(client):
    for i in range(3):
        demo(client)  # idempotent
    body = client.get("/api/v1/repositories?limit=1&offset=0").json()
    assert set(body) == {"items", "total", "limit", "offset"} and body["limit"] == 1 and len(body["items"]) == 1 and body["total"] >= 1


def test_file_endpoint_cannot_read_outside_the_index(client):
    rid, _ = demo(client)
    for p in ["../../../../etc/passwd", "/etc/passwd", "inventory/../../etc/passwd", "..%2f..%2fetc%2fpasswd", "inventory/pricing.py\x00.txt"]:
        r = client.get(f"/api/v1/repositories/{rid}/file", params={"path": p})
        assert r.status_code in (404, 422) and "root:" not in r.text
    ok = client.get(f"/api/v1/repositories/{rid}/file", params={"path": "inventory/pricing.py", "start": 8, "end": 12}).json()
    assert ok["start_line"] == 8 and "def apply_discount" in ok["content"] and ok["total_lines"] > 12


def test_api_never_returns_secrets(client, monkeypatch):
    monkeypatch.setenv("CODEMIND_LLM_API_KEY", "sk-super-secret-value")
    monkeypatch.setenv("CODEMIND_ADMIN_TOKEN", "admin-secret-value")
    get_settings.cache_clear()
    for path in ["/api/v1/settings", "/api/v1/settings/status", "/api/v1/dashboard"]:
        assert "sk-super-secret-value" not in client.get(path).text and "admin-secret-value" not in client.get(path).text
    s = client.get("/api/v1/settings").json()
    assert s["llm_api_key_configured"] is True and "llm_api_key" not in s
    client.put("/api/v1/settings", json={"llm_provider": "openai_compatible", "llm_model": "m"})
    import json

    saved = (get_settings().data_dir / "runtime_settings.json").read_text()
    assert "sk-super-secret-value" not in saved and json.loads(saved)["llm_model"] == "m"
    assert client.put("/api/v1/settings", json={"llm_api_key": "x"}).status_code == 422  # keys cannot be set via the API


def test_settings_validation(client):
    assert client.put("/api/v1/settings", json={"retrieval_weights": {"bogus": 1}}).status_code == 422
    assert client.put("/api/v1/settings", json={"retrieval_weights": {"graph": 99}}).status_code == 422
    assert client.put("/api/v1/settings", json={"sandbox_timeout_seconds": 1}).status_code == 422
    r = client.put("/api/v1/settings", json={"retrieval_weights": {"graph": 0.4}, "context_budget_chars": 5000})
    assert r.status_code == 200 and r.json()["retrieval_weights"]["graph"] == 0.4 and r.json()["context_budget_chars"] == 5000


@pytest.fixture()
def secured(env, monkeypatch):
    monkeypatch.setenv("CODEMIND_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CODEMIND_ADMIN_TOKEN", "root-token-for-tests")
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app(f"sqlite:///{env / 'sec.db'}")) as c:
        yield c


def H(t):
    return {"Authorization": f"Bearer {t}"}


def test_authentication_required_when_enabled(secured):
    h = secured.get("/api/v1/health")  # intentionally public for monitoring; must not leak data
    assert h.status_code == 200 and set(h.json()) == {"status", "database"}
    assert secured.get("/api/v1/repositories").status_code == 401
    assert secured.get("/api/v1/repositories", headers=H("wrong")).status_code == 401
    assert secured.get("/api/v1/repositories", headers={"Authorization": "Basic abc"}).status_code == 401
    assert secured.get("/api/v1/repositories", headers=H("root-token-for-tests")).status_code == 200


def test_roles_are_enforced_and_tokens_are_hashed(secured):
    admin = H("root-token-for-tests")
    v = secured.post("/api/v1/users", json={"username": "vera", "role": "viewer"}, headers=admin).json()
    d = secured.post("/api/v1/users", json={"username": "dev", "role": "developer"}, headers=admin).json()
    assert v["token"].startswith("cm_")
    assert secured.get("/api/v1/repositories", headers=H(v["token"])).status_code == 200
    assert secured.post("/api/v1/demo/load", headers=H(v["token"])).status_code == 403  # viewer cannot mutate
    assert secured.put("/api/v1/settings", json={"graph_depth": 1}, headers=H(d["token"])).status_code == 403  # developer is not admin
    assert secured.get("/api/v1/audit", headers=H(d["token"])).status_code == 403
    assert secured.post("/api/v1/demo/load", headers=H(d["token"])).status_code == 201
    assert secured.post("/api/v1/users", json={"username": "x"}, headers=H(d["token"])).status_code == 403
    assert secured.get("/api/v1/audit", headers=admin).status_code == 200
    with session_scope() as s:
        row = s.query(User).filter_by(username="vera").one()
        assert row.token_hash == hashlib.sha256(v["token"].encode()).hexdigest() and v["token"] not in row.token_hash
    assert secured.post("/api/v1/users", json={"username": "vera"}, headers=admin).status_code == 409


def test_unauthenticated_websocket_rejected_when_auth_enabled(secured):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with secured.websocket_connect("/api/v1/ws/events"):
            pass
    with secured.websocket_connect("/api/v1/ws/events?token=root-token-for-tests") as ws:
        assert ws.receive_json()["type"] == "hello"


def test_audit_trail_records_sensitive_actions(client):
    rid, task = demo(client)
    inv = client.post("/api/v1/investigations", json={"repository_id": rid, "title": "t", "description": task["description"], "failing_tests": task["relevant_tests"]}).json()
    p = client.post(f"/api/v1/investigations/{inv['id']}/patches", json={"diff": "x"})
    assert p.status_code == 422
    from tests.conftest import tree_hashes  # noqa: F401

    pid = client.post(f"/api/v1/investigations/{inv['id']}/patches", json={"diff": _oracle(rid, client)}).json()["id"]
    assert client.post(f"/api/v1/patches/{pid}/export", json={"approve": False}).status_code == 403
    assert client.post(f"/api/v1/patches/{pid}/export", json={"approve": True}).status_code == 200
    with session_scope() as s:
        actions = [a.action for a in s.query(AuditEvent).order_by(AuditEvent.id)]
    assert {"investigation.created", "patch.created", "patch.exported"} <= set(actions)


def _oracle(rid, client):
    from app.experiments.adapters.synthetic import TASK_SPECS, build_task_repo
    import tempfile
    from pathlib import Path

    return build_task_repo(Path(tempfile.mkdtemp()) / "r", TASK_SPECS[0])["oracle_fix_diff"]


def test_patch_endpoints_validate_input(client):
    rid, task = demo(client)
    inv = client.post("/api/v1/investigations", json={"repository_id": rid, "title": "t", "description": "d"}).json()["id"]
    both = client.post(f"/api/v1/investigations/{inv}/patches", json={"diff": "x", "from_analysis": True})
    neither = client.post(f"/api/v1/investigations/{inv}/patches", json={})
    assert both.status_code == 422 and neither.status_code == 422
    evil = client.post(f"/api/v1/investigations/{inv}/patches", json={"diff": "--- a/../x.py\n+++ b/../x.py\n@@ -1 +1 @@\n-a\n+b\n"})
    assert evil.status_code == 422 and evil.json()["error"]["code"] == "security_violation"
    no_model = client.post(f"/api/v1/investigations/{inv}/patches", json={"from_analysis": True})
    assert no_model.status_code == 409 and no_model.json()["error"]["code"] == "no_model_patch"
    assert client.post("/api/v1/investigations", json={"repository_id": rid, "title": "t"}).status_code == 422  # empty report
    assert client.post(f"/api/v1/patches/999/verify").status_code == 404
