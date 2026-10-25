# Windows + VS Code setup

Prerequisites: Python 3.11+ (3.13 used in development), Node.js 20+, Git, VS Code (extensions: Python, ESLint optional). Docker Desktop is optional but recommended for patch verification.

```powershell
git clone <your copy> codemind; cd codemind
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy .env.example .env
python -m pytest -q
cd frontend; npm install; npm run build; cd ..
python scripts\run_backend.py
```
Open http://127.0.0.1:8000. In VS Code, choose the `.venv` interpreter (Ctrl+Shift+P → Python: Select Interpreter).

Development with hot reload: terminal 1 `python scripts\run_backend.py`; terminal 2 `cd frontend; npm run dev` → http://127.0.0.1:5173.

## Sandbox
- **Docker (preferred):** install Docker Desktop, start it, leave `CODEMIND_SANDBOX_MODE=auto`. Settings → status should show `docker`. Mounted drives need file sharing enabled.
- **Local mode is NOT available on Windows** (it needs POSIX `preexec_fn` and resource limits). The backend reports no sandbox unless Docker is reachable. Use WSL2 with the Linux instructions if you cannot run Docker.
- Neither available: verification reports `VERIFICATION_INCOMPLETE`.

## Notes
- Register repositories by Windows path (`C:\Users\you\project`). Paths are resolved on the machine running the backend.
- Model keys: set `CODEMIND_LLM_API_KEY` in `.env` (never in the UI), then choose the provider in Settings.
- The Windows code path (path handling, Docker invocation) has **not been tested** by the author; development and all tests ran on Linux.
- Port in use: change `CODEMIND_PORT`.
