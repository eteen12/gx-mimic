"""Session directory management. Layout ($GX_MIMIC_HOME, design-contract.md
section 1):
    gxconfig/guitarix/{banks,IR,gx_head_rc}
    sessions/<slug>/{session.json,target.json,target-48k.wav,preset.json,history.jsonl,renders/}
    cache/{atlas/,fingerprints/<sha256>.json}
    capabilities.json  jack-restore.json  logs/  venv/
`$GX_MIMIC_HOME/current` (a plain text file containing a slug) names the
active session.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from gxmimic.errors import GxError

SCHEMA_SESSION = "gx-mimic/session/1"


def default_home() -> Path:
    xdg_state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(xdg_state) / "gx-mimic"


def resolve_home(home_arg: str | None = None) -> Path:
    if home_arg:
        return Path(home_arg).expanduser()
    env_home = os.environ.get("GX_MIMIC_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return default_home()


def ensure_home(home: Path) -> Path:
    home = Path(home)
    for sub in ("gxconfig", "sessions", "cache/atlas", "cache/fingerprints", "logs"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    return home


def slugify(artist: str, song: str) -> str:
    def clean(s: str) -> str:
        s = s.strip().lower()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        return s.strip("-")
    return f"{clean(artist)}-{clean(song)}" if artist else clean(song)


def session_dir(home: Path, slug: str) -> Path:
    return Path(home) / "sessions" / slug


def session_json_path(home: Path, slug: str) -> Path:
    return session_dir(home, slug) / "session.json"


def current_path(home: Path) -> Path:
    return Path(home) / "current"


def get_current_slug(home: Path) -> str | None:
    p = current_path(home)
    if not p.is_file():
        return None
    slug = p.read_text().strip()
    return slug or None


def set_current_slug(home: Path, slug: str) -> None:
    ensure_home(home)
    current_path(home).write_text(slug)


def list_sessions(home: Path) -> list[dict]:
    home = Path(home)
    sessions_dir = home / "sessions"
    if not sessions_dir.is_dir():
        return []
    out = []
    current = get_current_slug(home)
    for d in sorted(sessions_dir.iterdir()):
        sj = d / "session.json"
        if not sj.is_file():
            continue
        try:
            data = json.loads(sj.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        data["is_current"] = (d.name == current)
        out.append(data)
    return out


def new_session(home: Path, song: str, artist: str = "", slug: str | None = None) -> dict:
    home = ensure_home(Path(home))
    slug = slug or slugify(artist, song)
    d = session_dir(home, slug)
    if d.exists():
        raise GxError("usage", f"session already exists: {slug!r}", hint="use `session use` or pick a different slug")
    d.mkdir(parents=True)
    (d / "renders").mkdir(exist_ok=True)
    data = {
        "schema": SCHEMA_SESSION,
        "slug": slug,
        "song": song,
        "artist": artist,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    session_json_path(home, slug).write_text(json.dumps(data, indent=2))
    set_current_slug(home, slug)
    return data


def load_session(home: Path, slug: str) -> dict:
    p = session_json_path(home, slug)
    if not p.is_file():
        raise GxError("usage", f"no such session: {slug!r}")
    return json.loads(p.read_text())


def use_session(home: Path, slug: str) -> dict:
    data = load_session(home, slug)
    set_current_slug(home, slug)
    return data


def delete_session(home: Path, slug: str) -> None:
    import shutil
    d = session_dir(home, slug)
    if not d.is_dir():
        raise GxError("usage", f"no such session: {slug!r}")
    with lock_session(home, slug):
        shutil.rmtree(d)
    if get_current_slug(home) == slug:
        p = current_path(home)
        if p.exists():
            p.unlink()


def resolve_session_slug(home: Path, slug_arg: str | None) -> str:
    if slug_arg:
        return slug_arg
    slug = get_current_slug(home)
    if not slug:
        raise GxError("usage", "no session specified and no current session set",
                       hint="run `gx-mimic session new --song ... --artist ...` first")
    return slug


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------
@contextmanager
def lock_session(home: Path, slug: str, timeout_s: float = 0.0):
    """flock sessions/<slug>/.lock. Raises GxError(refused, exit_code=7) if
    already held (design-contract.md section 6, safety rule 6)."""
    d = session_dir(home, slug)
    d.mkdir(parents=True, exist_ok=True)
    lock_path = d / ".lock"
    f = open(lock_path, "a+")
    try:
        deadline = time.time() + timeout_s
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    f.seek(0)
                    holder = f.read().strip() or "unknown"
                    raise GxError("refused", f"session {slug!r} is locked (held by pid {holder})",
                                  hint="wait for the other gx-mimic process to finish, or check for a stale lock")
                time.sleep(0.05)
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()
