#!/usr/bin/env python3
"""Run the aggregator natively — no Docker.

Docker was packaging, not architecture: the service needs nothing from a
container except its dependencies and a ZIP database. This runner provides
both on the user's machine:

- a private venv at ~/.config/teetime/venv (built once, reused; never
  touches the user's own Python packages)
- the ZIP centroid database at ~/.config/teetime/zips.sqlite (downloaded
  from the Census gazetteer on first start — the one step that needs network)
- uvicorn bound to 127.0.0.1 only, same invariant as the compose file: this
  process receives credentials in request bodies and must not be reachable
  off-host.

The in-process cache replaces Redis (see MemoryCache in app/main.py), so
there is exactly one process to manage. First start takes a couple of
minutes; every start after that is seconds. Docker remains available for
those who prefer it — `docker compose -f service/docker-compose.yml up -d`
serves the same port, and start() is a no-op when anything healthy is
already listening.

Usage:
    python3 scripts/serve.py start      # background (what search.py calls)
    python3 scripts/serve.py stop
    python3 scripts/serve.py status
    python3 scripts/serve.py run        # foreground, logs to the terminal
"""
from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import time
import venv
from pathlib import Path

import prefs as prefs_mod

SERVICE_URL = "http://127.0.0.1:8077"
PORT = "8077"

CONFIG_DIR = prefs_mod.CONFIG_DIR
VENV_DIR = CONFIG_DIR / "venv"
ZIP_DB = CONFIG_DIR / "zips.sqlite"
PID_FILE = CONFIG_DIR / "service.pid"
LOG_FILE = CONFIG_DIR / "logs" / "service.log"

SKILL_DIR = Path(__file__).resolve().parent.parent
SERVICE_DIR = SKILL_DIR / "service"
REQUIREMENTS = SERVICE_DIR / "requirements.txt"
# Stamped after a successful install so a changed requirements file triggers
# a reinstall and an unchanged one costs nothing.
STAMP = VENV_DIR / ".requirements.sha1"


def say(msg: str) -> None:
    print(f"[serve] {msg}", file=sys.stderr)


def healthy(timeout: float = 2.0) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"{SERVICE_URL}/health", timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def venv_python() -> Path:
    return VENV_DIR / "bin" / "python"


def ensure_venv() -> None:
    want = hashlib.sha1(REQUIREMENTS.read_bytes()).hexdigest()
    if venv_python().exists() and STAMP.exists() and STAMP.read_text() == want:
        return
    say("first run: building the service environment (one time, ~2 min)")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    venv.create(VENV_DIR, with_pip=True, clear=not venv_python().exists())
    r = subprocess.run(
        [str(venv_python()), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"dependency install failed:\n{r.stderr[-2000:]}")
    STAMP.write_text(want)


def ensure_zip_db() -> None:
    if ZIP_DB.exists():
        return
    say("first run: downloading the ZIP code database (~2 MB)")
    r = subprocess.run(
        [str(venv_python()), str(SERVICE_DIR / "scripts" / "build_zip_db.py"),
         "--out", str(ZIP_DB)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"ZIP database build failed:\n{r.stderr[-2000:]}")


def service_env() -> dict:
    env = dict(os.environ)
    env["TEETIME_ZIP_DB"] = str(ZIP_DB)
    env.pop("REDIS_URL", None)  # unset → in-process cache
    return env


def uvicorn_cmd() -> list[str]:
    return [
        str(venv_python()), "-m", "uvicorn", "app.main:app",
        # Loopback only — same invariant as the compose file.
        "--host", "127.0.0.1", "--port", PORT, "--log-level", "warning",
    ]


def start() -> int:
    if healthy():
        say("service already up")
        return 0
    ensure_venv()
    ensure_zip_db()
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as log:
        proc = subprocess.Popen(
            uvicorn_cmd(), cwd=SERVICE_DIR, env=service_env(),
            stdout=log, stderr=log, start_new_session=True,
        )
    PID_FILE.write_text(str(proc.pid))
    for _ in range(30):
        if healthy():
            say(f"service up on {SERVICE_URL} (pid {proc.pid})")
            return 0
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    say(f"service failed to start — see {LOG_FILE}")
    return 1


def stop() -> int:
    if not PID_FILE.exists():
        say("no pid file; nothing to stop (Docker stack, if any, is separate)")
        return 0
    pid = int(PID_FILE.read_text().strip())
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        # Wait for the port to actually free so stop-then-start can't race a
        # dying process still holding the socket.
        for _ in range(20):
            if not healthy(timeout=0.5):
                break
            time.sleep(0.25)
        say(f"stopped pid {pid}")
    except (ProcessLookupError, PermissionError):
        say(f"pid {pid} was not running")
    PID_FILE.unlink()
    return 0


def status() -> int:
    up = healthy()
    pid = PID_FILE.read_text().strip() if PID_FILE.exists() else None
    say(f"health: {'up' if up else 'down'}"
        + (f" (native pid {pid})" if up and pid else ""))
    return 0 if up else 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "start":
        return start()
    if cmd == "stop":
        return stop()
    if cmd == "status":
        return status()
    if cmd == "run":
        ensure_venv()
        ensure_zip_db()
        os.chdir(SERVICE_DIR)
        os.execve(uvicorn_cmd()[0],
                  uvicorn_cmd()[:-2] + ["--log-level", "info"], service_env())
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
