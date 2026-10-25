"""Start the CodeMind API on localhost (default 127.0.0.1:8000). Usage: python scripts/run_backend.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn  # noqa: E402

from app.core.config import get_settings  # noqa: E402

if __name__ == "__main__":
    s = get_settings()
    if s.host not in ("127.0.0.1", "localhost", "::1") and not s.auth_required:
        raise SystemExit("Refusing to bind a non-local address without CODEMIND_AUTH_REQUIRED=true (see docs/threat_model.md).")
    uvicorn.run("app.main:get_app", factory=True, host=s.host, port=s.port, log_level="info")
