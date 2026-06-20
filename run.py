#!/usr/bin/env python3
"""
SOInsight — one command to run backend + frontend together.

    python run.py            # dev: backend (:8000, hot-reload) + Vite UI (:5173)
    python run.py --prod     # build UI once, serve everything from ONE process (:8000)
    python run.py --setup    # just install deps (venv + npm) and exit
    python run.py --no-open  # don't open a browser

Cross-platform (macOS / Linux / Windows) and dependency-free — it only uses the
Python standard library. The first run auto-creates the virtualenv, installs the
backend and frontend dependencies, and creates backend/.env from the example if
it doesn't exist (existing .env files are never overwritten).
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = ROOT / ".venv"
IS_WINDOWS = platform.system() == "Windows"

BACKEND_PORT = 8000
FRONTEND_PORT = 5173
HEALTH_URL = f"http://localhost:{BACKEND_PORT}/health"


# ── small console helpers ─────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    if IS_WINDOWS or not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def info(msg: str) -> None:
    print(_c("36", "==>"), msg, flush=True)


def warn(msg: str) -> None:
    print(_c("33", "WARN:"), msg, flush=True)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def npm_cmd() -> str | None:
    return shutil.which("npm.cmd") if IS_WINDOWS else shutil.which("npm")


# ── setup (idempotent) ────────────────────────────────────────────────────────

def ensure_venv() -> None:
    if not venv_python().exists():
        info("Creating Python virtualenv (.venv)…")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    info("Installing backend dependencies…")
    py = str(venv_python())
    subprocess.run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)
    # Editable install of the backend package; fall back to requirements.txt.
    if (BACKEND / "pyproject.toml").exists():
        subprocess.run([py, "-m", "pip", "install", "--quiet", "-e", str(BACKEND)], check=True)
    elif (BACKEND / "requirements.txt").exists():
        subprocess.run(
            [py, "-m", "pip", "install", "--quiet", "-r", str(BACKEND / "requirements.txt")],
            check=True,
        )


def ensure_frontend_deps() -> None:
    npm = npm_cmd()
    if npm is None:
        warn("npm not found — install Node.js 18+ from https://nodejs.org to run the UI.")
        return
    if not (FRONTEND / "node_modules").exists():
        info("Installing frontend dependencies (npm install)…")
        subprocess.run([npm, "install"], cwd=FRONTEND, check=True)


def ensure_env() -> None:
    env = BACKEND / ".env"
    example = BACKEND / ".env.example"
    if not env.exists() and example.exists():
        shutil.copyfile(example, env)
        warn(f"Created {env.relative_to(ROOT)} from the example — add your SO_API_KEY before fetching.")
    (BACKEND / "data").mkdir(parents=True, exist_ok=True)


def setup() -> None:
    ensure_venv()
    ensure_frontend_deps()
    ensure_env()


# ── process orchestration ─────────────────────────────────────────────────────

class Proc:
    """A labelled child process whose output is streamed with a prefix."""

    def __init__(self, name: str, cmd: list[str], cwd: Path, color: str) -> None:
        self.name = name
        self.color = color
        kwargs: dict = dict(
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "1"},
        )
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True  # own process group → clean group kill
        self.popen = subprocess.Popen(cmd, **kwargs)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        prefix = _c(self.color, f"[{self.name}]")
        assert self.popen.stdout is not None
        for line in self.popen.stdout:
            print(prefix, line.rstrip(), flush=True)

    def stop(self) -> None:
        if self.popen.poll() is not None:
            return
        try:
            if IS_WINDOWS:
                self.popen.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(os.getpgid(self.popen.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            self.popen.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                if IS_WINDOWS:
                    self.popen.kill()
                else:
                    os.killpg(os.getpgid(self.popen.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass


def wait_for_health(timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def open_browser(url: str, no_open: bool) -> None:
    if not no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass


def run_until_interrupt(procs: list[Proc]) -> None:
    def shutdown(*_a: object) -> None:
        print()
        info("Shutting down…")
        for p in procs:
            p.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, shutdown)
    try:
        while True:
            for p in procs:
                if p.popen.poll() is not None:
                    warn(f"{p.name} exited (code {p.popen.returncode}); stopping the rest.")
                    shutdown()
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown()


# ── modes ─────────────────────────────────────────────────────────────────────

def run_dev(no_open: bool) -> None:
    npm = npm_cmd()
    if npm is None:
        warn("npm not found — falling back to single-process --prod is not possible without a build. "
             "Install Node.js to use dev mode.")
        sys.exit(1)
    info(f"DEV mode — backend :{BACKEND_PORT} (hot-reload) + frontend :{FRONTEND_PORT}")
    backend = Proc(
        "backend",
        [str(venv_python()), "-m", "uvicorn", "app.main:app", "--reload", "--port", str(BACKEND_PORT)],
        cwd=BACKEND, color="32",
    )
    if wait_for_health():
        info(f"Backend healthy at http://localhost:{BACKEND_PORT}")
    frontend = Proc("frontend", [npm, "run", "dev"], cwd=FRONTEND, color="35")
    time.sleep(2)
    open_browser(f"http://localhost:{FRONTEND_PORT}", no_open)
    print()
    info(f"SOInsight (dev) → UI http://localhost:{FRONTEND_PORT}   API http://localhost:{BACKEND_PORT}/docs")
    info("Press Ctrl+C to stop both.")
    run_until_interrupt([backend, frontend])


def run_prod(no_open: bool) -> None:
    npm = npm_cmd()
    if npm is not None:
        info("Building the UI…")
        subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True)
    else:
        warn("npm not found — serving a previously built UI from frontend/dist if present.")
    info(f"Starting SOInsight (single process) on http://localhost:{BACKEND_PORT}")
    backend = Proc(
        "soinsight",
        [str(venv_python()), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
        cwd=BACKEND, color="32",
    )
    if wait_for_health():
        info("Up and healthy.")
    open_browser(f"http://localhost:{BACKEND_PORT}", no_open)
    print()
    info(f"SOInsight → http://localhost:{BACKEND_PORT}   (API docs: /docs)")
    info("Press Ctrl+C to stop.")
    run_until_interrupt([backend])


# ── entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Run SOInsight backend + frontend together.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prod", action="store_true", help="Build UI and serve from one process (:8000).")
    mode.add_argument("--setup", action="store_true", help="Install dependencies and exit.")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser.")
    parser.add_argument("--skip-setup", action="store_true", help="Skip the dependency check.")
    args = parser.parse_args()

    print(_c("1", "SOInsight launcher"), f"({ROOT})")

    if not args.skip_setup:
        setup()
    if args.setup:
        info("Setup complete. Run `python run.py` to start.")
        return

    if args.prod:
        run_prod(args.no_open)
    else:
        run_dev(args.no_open)


if __name__ == "__main__":
    main()
