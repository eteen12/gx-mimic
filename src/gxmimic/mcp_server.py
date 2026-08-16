"""gx-mimic MCP server: stdio transport, imports gxmimic.api in-process for
everything except render (which api.render() itself shells out to
`python -m gxmimic.render_worker` for, per D10 -- a JACK crash must not be
able to take this server down).

Note on the `mcp` SDK: this codebase's installed `mcp` package renamed the
classic `FastMCP` class to `mcp.server.mcpserver.MCPServer` (same
decorator-based `@server.tool()` API). We import it as `FastMCP` for
readability/continuity with the design contract's wording; it is the same
thing.

Exactly the 10 tools from design-contract.md section 7. Descriptions are
literal agent-facing prompts, not just documentation -- they tell the
calling agent when to call the tool and what to do with the result.
`gx_score`/`gx_fit` are deliberately NOT separate tools (folded into
gx_render_and_score / gx_match) so agents don't invent ad-hoc loops.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer as FastMCP

from gxmimic import api
from gxmimic import session as sessionmod
from gxmimic.errors import GxError

server = FastMCP(
    name="gx-mimic",
    instructions=(
        "Tools for matching a Guitarix amp-sim preset to a reference guitar tone. "
        "Call gx_doctor first. The reference audio MUST be an isolated/solo guitar "
        "recording, never a full mix or minus-guitar backing track."
    ),
)


def _home() -> Path:
    home_env = os.environ.get("GX_MIMIC_HOME")
    home = sessionmod.resolve_home(home_env)
    sessionmod.ensure_home(home)
    return home


def _current_slug(session: str | None) -> str | None:
    if session:
        return session
    return sessionmod.get_current_slug(_home())


def _append_history(slug: str | None, tool: str, note: str = "") -> None:
    if not slug:
        return
    import hashlib
    import time
    d = sessionmod.session_dir(_home(), slug)
    if not d.is_dir():
        return
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": tool, "note": note}
    with open(d / "history.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def _guard(fn):
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except GxError as e:
            return {"error": e.to_json()}
        except Exception as e:  # noqa: BLE001
            return {"error": GxError("internal", f"{type(e).__name__}: {e}").to_json()}
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


@server.tool()
@_guard
def gx_doctor(deep: bool = False) -> dict[str, Any]:
    """Call this FIRST, before anything else in this skill. Checks that
    guitarix, ffmpeg, JACK and the isolated config tree are all usable.
    If the result's `blocking` list is non-empty, STOP: tell the user what's
    missing in plain language and do not attempt to render or match until
    it's resolved. `deep=true` also empirically detects the RPC vs. file
    write path (takes a few seconds); safe to run without it first."""
    return api.doctor(_home(), deep=deep)


@server.tool()
@_guard
def gx_session(action: str, song: str | None = None, artist: str | None = None,
               slug: str | None = None) -> dict[str, Any]:
    """Manage the working session for a song. Call action="new" right after
    the user has picked a backing track (song + artist), before analyzing
    anything -- it creates the on-disk state everything else writes into
    and becomes the "current" session. Call action="show" any time you need
    to recover state after a context compaction (it returns what's been
    done so far). Other actions: "list", "use" (switch session), "delete"."""
    home = _home()
    if action == "new":
        if not song:
            raise GxError("usage", "session new requires `song`")
        return api.session_new(home, song, artist=artist or "", slug=slug)
    if action == "list":
        return {"sessions": api.session_list(home)}
    if action == "show":
        return api.session_show(home, slug or _current_slug(None) or "")
    if action == "use":
        if not slug:
            raise GxError("usage", "session use requires `slug`")
        return api.session_use(home, slug)
    if action == "delete":
        if not slug:
            raise GxError("usage", "session delete requires `slug`")
        api.session_delete(home, slug)
        return {"deleted": slug}
    raise GxError("usage", f"unknown session action: {action!r}")


@server.tool()
@_guard
def gx_analyze_target(audio_path: str, session: str | None = None) -> dict[str, Any]:
    """Analyze a reference audio clip into a tone fingerprint. The audio
    MUST be an ISOLATED or solo guitar recording (a DI, a stem, or a clean
    solo take) -- if it's a full band mix or a minus-guitar backing track,
    REFUSE and ask the user for an isolated track instead (the result's
    `warnings` field flags likely full-mix/backing-track signatures; take
    those seriously). After analyzing, describe the tone in plain language
    using `descriptors` (brightness, gain_class, warmth, etc.) -- don't just
    dump the JSON at the user."""
    slug = _current_slug(session)
    fp = api.analyze(audio_path, save=True, session=slug, home=_home())
    _append_history(slug, "gx_analyze_target", f"gain_class={fp['descriptors'].get('gain_class')}")
    return fp


@server.tool()
@_guard
def gx_build_preset(target: str, name: str | None = None, hint: str | None = None,
                     session: str | None = None) -> dict[str, Any]:
    """Build a starting Guitarix preset from a tone fingerprint (or an audio
    path/fingerprint JSON file). Instant -- no rendering happens here. Read
    the returned `rationale` list to the user in plain language (which amp
    model / cab / drive level was picked and why) before moving on."""
    slug = _current_slug(session)
    preset = api.build(target, name=name, hint=hint, session=slug, home=_home())
    _append_history(slug, "gx_build_preset", preset.get("name", ""))
    return preset


@server.tool()
@_guard
def gx_render_and_score(target: str | None = None, session: str | None = None) -> dict[str, Any]:
    """Render the current session's preset through real Guitarix and score
    it against the target. This is the ONLY slow operation (5-30 seconds) --
    tell the user you're rendering before calling it. Returns a `verdict`
    (plain-English lines) and `suggested_tweaks` (ready to pass straight
    into gx_tweak_preset without you having to compute anything)."""
    home = _home()
    slug = _current_slug(session)
    if not slug:
        raise GxError("usage", "no session; call gx_session(action='new') first")
    preset_path = sessionmod.session_dir(home, slug) / "preset.json"
    if not preset_path.is_file():
        raise GxError("usage", "no preset in this session yet; call gx_build_preset first")
    preset = json.loads(preset_path.read_text())
    target_fp = api.load_fingerprint_arg(target, home) if target else json.loads(
        (sessionmod.session_dir(home, slug) / "target.json").read_text())

    render_result = api.render(preset, home, session=slug)
    score_result = api.score(target_fp, render_result["fingerprint"])
    _append_history(slug, "gx_render_and_score", f"match={score_result['match']:.1f}")
    return {"render": render_result, "score": score_result}


@server.tool()
@_guard
def gx_match(target: str | None = None, session: str | None = None, rounds: int = 2,
             budget_s: float = 300.0) -> dict[str, Any]:
    """Run the bounded auto-match loop: drive/gain search, EQ fit, and a
    verification render, repeated for a small number of rounds (default 2 --
    call this ONCE after gx_build_preset, not in a loop yourself). ALWAYS
    returns a `best` preset even if it didn't fully converge. After this
    call, stop and hand control back to the user (ask them to try the tone
    and give feedback) rather than calling gx_match again immediately."""
    home = _home()
    slug = _current_slug(session)
    if not slug:
        raise GxError("usage", "no session; call gx_session(action='new') first")
    target_fp = api.load_fingerprint_arg(target, home) if target else json.loads(
        (sessionmod.session_dir(home, slug) / "target.json").read_text())
    result = api.match(target_fp, home, session=slug, rounds=rounds, budget_s=budget_s)
    if result.get("best"):
        (sessionmod.session_dir(home, slug) / "preset.json").write_text(
            json.dumps(result["best"]["preset"], indent=2, default=str))
    _append_history(slug, "gx_match", f"converged={result['converged']}")
    return result


@server.tool()
@_guard
def gx_tweak_preset(deltas: dict[str, float], allow_structural: bool = False,
                     session: str | None = None) -> dict[str, Any]:
    """Apply word-scale tone tweaks to the current preset: a dict of
    descriptor -> notch count in [-3, 3] (fractional OK). Descriptors:
    brightness/darkness, presence, fizz, mids, bass, warmth, gain,
    tightness, reverb, compression. Map user words to notches yourself:
    "a bit brighter" = {"brightness": 1}, "way too much gain" =
    {"gain": -2}, "just a touch warmer" = {"warmth": 0.5}. If a requested
    change saturates a knob, it's reported in `clamped`; if a descriptor
    (usually "mids") would need a different amp model to go further, it's
    offered in `suggested_structural` instead of applied -- relay that
    offer to the user rather than silently ignoring it."""
    home = _home()
    slug = _current_slug(session)
    if not slug:
        raise GxError("usage", "no session; call gx_session(action='new') first")
    preset_path = sessionmod.session_dir(home, slug) / "preset.json"
    preset = json.loads(preset_path.read_text())
    result = api.tweak(preset, deltas, allow_structural=allow_structural)
    preset_path.write_text(json.dumps(result["preset"], indent=2, default=str))
    _append_history(slug, "gx_tweak_preset", json.dumps(deltas))
    return result


@server.tool()
@_guard
def gx_set_params(params: dict[str, Any], force: bool = False, session: str | None = None) -> dict[str, Any]:
    """Set raw Guitarix parameters directly. Only use this when the user
    names a specific control by its actual name/value (e.g. "set the tube
    to 6V6" or "amp.tonestack.Bass to 0.6") -- for anything expressed as a
    feeling/direction ("brighter", "more gain"), use gx_tweak_preset
    instead."""
    home = _home()
    slug = _current_slug(session)
    if not slug:
        raise GxError("usage", "no session; call gx_session(action='new') first")
    preset_path = sessionmod.session_dir(home, slug) / "preset.json"
    preset = json.loads(preset_path.read_text())
    result = api.set_params(preset, params, force=force)
    preset_path.write_text(json.dumps(result["preset"], indent=2, default=str))
    _append_history(slug, "gx_set_params", json.dumps(params))
    return result


@server.tool()
@_guard
def gx_show_preset(session: str | None = None) -> dict[str, Any]:
    """Show the current session's preset: a human-readable summary plus the
    full preset JSON. Use this to check state, or when the user asks
    "what's the tone right now" / "what have we changed"."""
    home = _home()
    slug = _current_slug(session)
    if not slug:
        raise GxError("usage", "no session; call gx_session(action='new') first")
    preset_path = sessionmod.session_dir(home, slug) / "preset.json"
    if not preset_path.is_file():
        raise GxError("usage", "no preset in this session yet; call gx_build_preset first")
    preset = json.loads(preset_path.read_text())
    return api.show(preset)


@server.tool()
@_guard
def gx_install_preset(yes: bool = False, replace: bool = False, session: str | None = None) -> dict[str, Any]:
    """Write the current preset into the user's real Guitarix config as
    bank "gx-mimic". ALWAYS ask the user to explicitly confirm before
    calling this with yes=true, and tell them to close Guitarix first if
    it's open (this refuses while it's running). Automatically backs up
    their existing config first and can be undone."""
    home = _home()
    slug = _current_slug(session)
    if not slug:
        raise GxError("usage", "no session; call gx_session(action='new') first")
    preset_path = sessionmod.session_dir(home, slug) / "preset.json"
    preset = json.loads(preset_path.read_text())
    result = api.install(preset, yes=yes, replace=replace)
    _append_history(slug, "gx_install_preset", "installed")
    return result


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
