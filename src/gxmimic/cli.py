"""gx-mimic CLI. JSON always goes to stdout (--pretty indents); all human/
progress text goes to stderr. Every subcommand maps 1:1 onto an api.py
function -- this module is argument parsing + dispatch only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from gxmimic import api
from gxmimic import schemas
from gxmimic import session as sessionmod
from gxmimic.errors import GxError


def _out(result: dict, pretty: bool) -> None:
    print(schemas.dumps(result, pretty=pretty))


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text())


def _preset_from_args(args) -> dict:
    if getattr(args, "preset", None):
        return _load_json(args.preset)
    home = sessionmod.resolve_home(args.home)
    slug = sessionmod.resolve_session_slug(home, args.session)
    p = sessionmod.session_dir(home, slug) / "preset.json"
    if not p.is_file():
        raise GxError("usage", f"no preset.json in session {slug!r}", hint="run `gx-mimic build` first")
    return json.loads(p.read_text())


def _fingerprint_from_target_arg(target: str, home: Path) -> dict:
    return api.load_fingerprint_arg(target, home)


def _target_fp_from_args(args, home: Path) -> dict:
    if getattr(args, "target", None):
        return _fingerprint_from_target_arg(args.target, home)
    slug = sessionmod.resolve_session_slug(home, args.session)
    p = sessionmod.session_dir(home, slug) / "target.json"
    if not p.is_file():
        raise GxError("usage", f"no target.json in session {slug!r}", hint="run `gx-mimic analyze --save` first")
    return json.loads(p.read_text())


def _preset_hash(preset: dict) -> str:
    return hashlib.sha256(json.dumps(preset.get("params", {}), sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# subcommand handlers -- each returns (result_dict, exit_code)
# ---------------------------------------------------------------------------
def cmd_doctor(args):
    home = sessionmod.resolve_home(args.home)
    result = api.doctor(home, deep=args.deep, restore_jack=args.restore_jack)
    exit_code = 3 if result["blocking"] else 0
    return result, exit_code


def cmd_analyze(args):
    home = sessionmod.resolve_home(args.home)
    result = api.analyze(args.audio, start=args.start, duration=args.duration,
                          label=args.label, save=args.save, session=args.session, home=home)
    return result, 0


def cmd_build(args):
    home = sessionmod.resolve_home(args.home)
    result = api.build(args.target, name=args.name, hint=args.hint, session=args.session, home=home)
    return result, 0


def cmd_render(args):
    home = sessionmod.resolve_home(args.home)
    preset = _preset_from_args(args)
    clips = args.clips.split(",") if args.clips else None
    _err("rendering through guitarix... (this can take several seconds)")
    result = api.render(preset, home, session=args.session, clips=clips, flat_eq=args.flat_eq,
                         no_reverb=args.no_reverb, write_path=args.write_path,
                         jack_policy=args.jack_policy, keep_alive=args.keep_alive)
    return result, 0


def cmd_score(args):
    home = sessionmod.resolve_home(args.home)
    target_fp = _fingerprint_from_target_arg(args.target, home)
    render_data = _load_json(args.render)
    render_fp = render_data.get("fingerprint", render_data)
    weights = json.loads(args.weights) if args.weights else None
    result = api.score(target_fp, render_fp, weights=weights)
    return result, 0


def cmd_fit(args):
    home = sessionmod.resolve_home(args.home)
    target_fp = _target_fp_from_args(args, home)
    preset = _preset_from_args(args)
    slug = sessionmod.resolve_session_slug(home, args.session) if (args.session or not args.target) else None

    flat_fp = None
    cache_path = None
    if slug:
        cache_path = sessionmod.session_dir(home, slug) / "flat_render_cache.json"
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text())
            if cached.get("preset_hash") == _preset_hash(preset):
                flat_fp = cached["fingerprint"]
    if flat_fp is None:
        _err("no fresh --flat-eq render cached; rendering one now...")
        rendered = api.render(preset, home, session=slug, flat_eq=True)
        flat_fp = rendered["fingerprint"]
        if cache_path:
            cache_path.write_text(json.dumps({"preset_hash": _preset_hash(preset), "fingerprint": flat_fp}, default=str))

    result = api.fit(target_fp, flat_fp, max_boost=args.max_boost, lam=args.lam, include_cab_eq=args.include_cab_eq)
    return result, 0


def cmd_tweak(args):
    preset = _preset_from_args(args)
    deltas = json.loads(args.deltas)
    result = api.tweak(preset, deltas, dry_run=args.dry_run, allow_structural=args.allow_structural)
    if not args.dry_run and args.session:
        home = sessionmod.resolve_home(args.home)
        slug = sessionmod.resolve_session_slug(home, args.session)
        (sessionmod.session_dir(home, slug) / "preset.json").write_text(json.dumps(result["preset"], indent=2, default=str))
    return result, 0


def cmd_set(args):
    preset = _preset_from_args(args)
    params = json.loads(args.params)
    result = api.set_params(preset, params, force=args.force)
    if args.session:
        home = sessionmod.resolve_home(args.home)
        slug = sessionmod.resolve_session_slug(home, args.session)
        (sessionmod.session_dir(home, slug) / "preset.json").write_text(json.dumps(result["preset"], indent=2, default=str))
    return result, 0


def cmd_match(args):
    home = sessionmod.resolve_home(args.home)
    target_fp = _target_fp_from_args(args, home)
    initial_preset = None
    if args.session:
        slug = sessionmod.resolve_session_slug(home, args.session)
        pp = sessionmod.session_dir(home, slug) / "preset.json"
        if pp.is_file():
            initial_preset = json.loads(pp.read_text())
    _err(f"matching, up to {args.rounds} rounds, budget {args.budget_s}s...")
    result = api.match(target_fp, home, session=args.session, rounds=args.rounds,
                        budget_s=args.budget_s, stop_at=args.stop_at, initial_preset=initial_preset)
    if args.session and result.get("best"):
        slug = sessionmod.resolve_session_slug(home, args.session)
        (sessionmod.session_dir(home, slug) / "preset.json").write_text(
            json.dumps(result["best"]["preset"], indent=2, default=str))
    exit_code = 0 if result["converged"] else 6
    return result, exit_code


def cmd_show(args):
    preset = _preset_from_args(args)
    vs = _load_json(args.vs) if args.vs else None
    result = api.show(preset, format=args.format, vs=vs)
    return result, 0


def cmd_install(args):
    if args.undo:
        result = api.install(preset=None, undo=True)
        return result, 0
    preset = _preset_from_args(args)
    result = api.install(preset, bank=args.bank, preset_name=args.preset_name, yes=args.yes, replace=args.replace)
    return result, 0


def cmd_session(args):
    home = sessionmod.resolve_home(args.home)
    if args.session_action == "new":
        result = api.session_new(home, args.song, artist=args.artist, slug=args.slug)
    elif args.session_action == "list":
        result = {"schema": "gx-mimic/session_list/1", "sessions": api.session_list(home)}
    elif args.session_action == "show":
        result = api.session_show(home, args.slug or sessionmod.resolve_session_slug(home, None))
    elif args.session_action == "use":
        result = api.session_use(home, args.slug)
    elif args.session_action == "delete":
        api.session_delete(home, args.slug)
        result = {"schema": "gx-mimic/session_delete/1", "deleted": args.slug}
    else:
        raise GxError("usage", f"unknown session action {args.session_action!r}")
    return result, 0


def cmd_probes(args):
    if args.probes_action == "list":
        result = api.probes_list()
    elif args.probes_action == "validate":
        result = api.probes_validate()
    elif args.probes_action == "use":
        result = api.probes_use(args.name, args.wav)
    else:
        raise GxError("usage", f"unknown probes action {args.probes_action!r}")
    return result, 0


def cmd_calibrate(args):
    home = sessionmod.resolve_home(args.home)
    result = api.calibrate(args.target, home, out=args.out)
    return result, 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------
def _common_parent() -> argparse.ArgumentParser:
    """Global options, usable either before or after the subcommand name
    (gx-mimic --pretty doctor  ==  gx-mimic doctor --pretty)."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--session", default=None, help="session slug (default: current)")
    common.add_argument("--home", default=None, help="override $GX_MIMIC_HOME")
    common.add_argument("--pretty", action="store_true", help="pretty-print JSON stdout")
    common.add_argument("--json", action="store_true", help="no-op: stdout is always JSON (kept for explicitness/compatibility)")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_parent()
    p = argparse.ArgumentParser(prog="gx-mimic", description="Match a Guitarix preset to a reference guitar tone.",
                                 parents=[common])
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", parents=[common])
    d.add_argument("--deep", action="store_true")
    d.add_argument("--restore-jack", action="store_true")
    d.set_defaults(func=cmd_doctor)

    a = sub.add_parser("analyze", parents=[common])
    a.add_argument("audio")
    a.add_argument("--start", type=float, default=None)
    a.add_argument("--duration", type=float, default=None)
    a.add_argument("--label", choices=["reference", "render"], default="reference")
    a.add_argument("--save", action="store_true")
    a.set_defaults(func=cmd_analyze)

    b = sub.add_parser("build", parents=[common])
    b.add_argument("--target", required=True)
    b.add_argument("--name", default=None)
    b.add_argument("--hint", default=None)
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("render", parents=[common])
    r.add_argument("--preset", default=None)
    r.add_argument("--clips", default=None, help="comma-separated: chord,chug,lead")
    r.add_argument("--flat-eq", action="store_true")
    r.add_argument("--no-reverb", action="store_true")
    r.add_argument("--write-path", choices=["auto", "rpc", "file"], default="auto")
    r.add_argument("--jack-policy", choices=["auto", "use-existing", "dummy"], default="auto")
    r.add_argument("--keep-alive", action="store_true")
    r.set_defaults(func=cmd_render)

    sc = sub.add_parser("score", parents=[common])
    sc.add_argument("--target", required=True)
    sc.add_argument("--render", required=True)
    sc.add_argument("--weights", default=None, help='JSON, e.g. {"spectral":0.65,"gain":0.25,"tightness":0.10}')
    sc.set_defaults(func=cmd_score)

    ft = sub.add_parser("fit", parents=[common])
    ft.add_argument("--target", default=None)
    ft.add_argument("--preset", default=None)
    ft.add_argument("--max-boost", type=float, default=12.0)
    ft.add_argument("--lambda", dest="lam", type=float, default=0.05)
    ft.add_argument("--include-cab-eq", action="store_true")
    ft.set_defaults(func=cmd_fit)

    tw = sub.add_parser("tweak", parents=[common])
    tw.add_argument("--preset", default=None)
    tw.add_argument("--deltas", required=True, help='JSON, e.g. {"brightness":1,"gain":-0.5}')
    tw.add_argument("--dry-run", action="store_true")
    tw.add_argument("--allow-structural", action="store_true")
    tw.set_defaults(func=cmd_tweak)

    se = sub.add_parser("set", parents=[common])
    se.add_argument("--preset", default=None)
    se.add_argument("--params", required=True, help="JSON param->value map")
    se.add_argument("--force", action="store_true")
    se.set_defaults(func=cmd_set)

    m = sub.add_parser("match", parents=[common])
    m.add_argument("--target", default=None)
    m.add_argument("--rounds", type=int, default=3)
    m.add_argument("--budget-s", type=float, default=300.0)
    m.add_argument("--stop-at", type=float, default=85.0)
    m.set_defaults(func=cmd_match)

    sh = sub.add_parser("show", parents=[common])
    sh.add_argument("--preset", default=None)
    sh.add_argument("--format", choices=["json", "table", "diff"], default="json")
    sh.add_argument("--vs", default=None)
    sh.set_defaults(func=cmd_show)

    ins = sub.add_parser("install", parents=[common])
    ins.add_argument("--preset", default=None)
    ins.add_argument("--bank", default="gx-mimic")
    ins.add_argument("--preset-name", dest="preset_name", default=None)
    ins.add_argument("--yes", action="store_true")
    ins.add_argument("--replace", action="store_true")
    ins.add_argument("--undo", action="store_true")
    ins.set_defaults(func=cmd_install)

    ses = sub.add_parser("session", parents=[common])
    ses_sub = ses.add_subparsers(dest="session_action", required=True)
    ses_new = ses_sub.add_parser("new", parents=[common])
    ses_new.add_argument("--song", required=True)
    ses_new.add_argument("--artist", default="")
    ses_new.add_argument("--slug", default=None)
    ses_sub.add_parser("list", parents=[common])
    ses_show = ses_sub.add_parser("show", parents=[common])
    ses_show.add_argument("slug", nargs="?", default=None)
    ses_use = ses_sub.add_parser("use", parents=[common])
    ses_use.add_argument("slug")
    ses_del = ses_sub.add_parser("delete", parents=[common])
    ses_del.add_argument("slug")
    ses.set_defaults(func=cmd_session)

    pr = sub.add_parser("probes", parents=[common])
    pr_sub = pr.add_subparsers(dest="probes_action", required=True)
    pr_sub.add_parser("list", parents=[common])
    pr_sub.add_parser("validate", parents=[common])
    pr_use = pr_sub.add_parser("use", parents=[common])
    pr_use.add_argument("name", choices=["chord", "chug", "lead"])
    pr_use.add_argument("wav")
    pr.set_defaults(func=cmd_probes)

    cal = sub.add_parser("calibrate", parents=[common])
    cal.add_argument("target", choices=["eqs", "tonestack", "cab"])
    cal.add_argument("--out", default=None)
    cal.set_defaults(func=cmd_calibrate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result, exit_code = args.func(args)
        _out(result, args.pretty)
        return exit_code
    except GxError as e:
        _out(e.to_json(), args.pretty)
        return e.exit_code
    except (json.JSONDecodeError, FileNotFoundError, ValueError) as e:
        # Malformed --deltas/--params/--weights JSON, or a referenced file
        # (--preset/--target/--render/--vs) that doesn't exist: both are
        # usage errors (exit 2), not internal crashes.
        err = GxError("usage", f"{type(e).__name__}: {e}")
        _out(err.to_json(), args.pretty)
        return err.exit_code
    except Exception as e:  # noqa: BLE001
        err = GxError("internal", f"{type(e).__name__}: {e}")
        _out(err.to_json(), args.pretty)
        return err.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
