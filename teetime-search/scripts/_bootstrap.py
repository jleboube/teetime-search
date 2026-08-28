"""Self-bootstrapping environment for the skill scripts.

Nobody should have to run pip — on a modern Mac a bare `pip install` fails
anyway (PEP 668 blocks installs into Homebrew/system Python). So every entry
script imports this module before its third-party imports: if the current
interpreter is missing anything, this builds the managed venv at
~/.config/teetime/venv (the same one the service runs from) and re-executes
the script under it. When the environment is already fine — the common case —
the cost is a few import-machinery lookups and no subprocess.

The venv holds both requirement sets (CLI + service) under one stamp, so
there is exactly one environment to reason about and `serve.py` reuses it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import venv
from pathlib import Path

CONFIG_DIR = Path("~/.config/teetime").expanduser()
VENV_DIR = CONFIG_DIR / "venv"
STAMP = VENV_DIR / ".requirements.sha1"

SKILL_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS = [
    SKILL_DIR / "requirements.txt",
    SKILL_DIR / "service" / "requirements.txt",
]

# What the CLI scripts themselves import. The service's deps are installed
# too, but their absence here shouldn't force a re-exec of a CLI script.
CLI_MODULES = ("httpx", "keyring", "rich")

# Breaks the loop if a re-exec somehow still lacks a module: the script then
# fails with a plain ImportError instead of exec-ing forever.
_REEXEC_GUARD = "TEETIME_BOOTSTRAPPED"


def venv_python() -> Path:
    sub = "Scripts" if os.name == "nt" else "bin"
    return VENV_DIR / sub / ("python.exe" if os.name == "nt" else "python")


def _stamp_value() -> str:
    h = hashlib.sha1()
    for req in REQUIREMENTS:
        h.update(req.read_bytes())
    return h.hexdigest()


def ensure_venv() -> None:
    """Build the managed venv and install both requirement sets. Cheap when
    the stamp matches; a changed requirements file triggers a reinstall."""
    want = _stamp_value()
    if venv_python().exists() and STAMP.exists() and STAMP.read_text() == want:
        return
    print("[setup] first run: preparing the environment (one time, ~2 min)",
          file=sys.stderr)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not venv_python().exists():
        venv.create(VENV_DIR, with_pip=True)
    cmd = [str(venv_python()), "-m", "pip", "install", "-q"]
    for req in REQUIREMENTS:
        cmd += ["-r", str(req)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"[setup] dependency install failed:\n{r.stderr[-2000:]}")
    STAMP.write_text(want)


def _missing() -> bool:
    return any(importlib.util.find_spec(m) is None for m in CLI_MODULES)


if _missing() and not os.environ.get(_REEXEC_GUARD):
    ensure_venv()
    env = {**os.environ, _REEXEC_GUARD: "1"}
    os.execve(str(venv_python()), [str(venv_python())] + sys.argv, env)
