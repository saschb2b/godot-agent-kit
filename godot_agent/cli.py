"""Command line entry point: `godot-agent <command>`."""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
from pathlib import Path

from . import __version__, api, sheet
from .check import check_project, format_check
from .env import (CACHE_DIR, CONFIG_PATH, GodotAgentError, find_godot, godot_version, load_config,
                  refresh_class_cache, resolve_project, save_config)
from .runner import format_summary, run_quick, run_scenarios


def _parse_watch(items: list[str] | None) -> dict:
    watch = {}
    for item in items or []:
        name, sep, expr = item.partition("=")
        if not sep:
            raise GodotAgentError(f"--watch needs name=expression, got: {item}")
        watch[name.strip()] = expr.strip()
    return watch


def cmd_doctor(args: argparse.Namespace) -> int:
    if args.print_godot:
        print(find_godot())
        return 0
    if args.set_godot:
        config = load_config()
        config["godot"] = str(Path(args.set_godot).expanduser().resolve())
        save_config(config)
        print(f"saved godot path to {CONFIG_PATH}")
    rows = []
    try:
        godot = find_godot()
        rows.append(("godot", True, f"{godot_version(godot)}  ({godot})"))
    except GodotAgentError as exc:
        rows.append(("godot", False, str(exc)))
    rows.append(("Xvfb (captures without a window)", bool(shutil.which("Xvfb")), shutil.which("Xvfb") or "sudo apt install xvfb"))
    rows.append(("ffmpeg (audio checks)", bool(shutil.which("ffmpeg")), shutil.which("ffmpeg") or "sudo apt install ffmpeg"))
    rows.append(("aseprite (sprite import)", bool(shutil.which("aseprite")), shutil.which("aseprite") or "missing"))
    rows.append(("blender (asset blender)", bool(shutil.which("blender")), shutil.which("blender") or "sudo snap install blender --classic"))
    rows.append(("Pillow (contact sheets)", sheet.Image is not None, "ok" if sheet.Image else "pip install pillow"))
    rows.append(("gdlint / gdformat", bool(shutil.which("gdlint")), shutil.which("gdlint") or "uv tool install gdtoolkit"))
    rows.append(("git (API docs fetch)", bool(shutil.which("git")), shutil.which("git") or "missing"))
    api_dirs = sorted(p.name for p in (CACHE_DIR / "api").glob("*") if (p / "index.json").exists())
    rows.append(("API index", bool(api_dirs), ", ".join(api_dirs) or "run: godot-agent api build"))
    for name, ok, detail in rows:
        print(f"  {'ok ' if ok else 'MISSING'}  {name:<34} {detail}")
    return 0 if all(ok for _, ok, _ in rows[:2]) else 1


def cmd_run(args: argparse.Namespace) -> int:
    capture_at = [int(x) for x in args.capture_at.split(",")] if args.capture_at else None
    summary = run_quick(args.project, scene=args.scene, frames=args.frames, capture_at=capture_at,
                        capture_every=args.capture_every, overlay=args.overlay, seed=args.seed,
                        watch=_parse_watch(args.watch), headless=args.headless, visible=args.visible,
                        timeout=args.timeout, refresh=not args.no_refresh, renderer=args.renderer)
    return _emit(summary, args.json)


def cmd_scenario(args: argparse.Namespace) -> int:
    inline = json.loads(args.inline) if args.inline else None
    summary = run_scenarios(args.project, paths=args.paths or None, inline=inline, jobs=args.jobs,
                            determinism=args.determinism, headless=args.headless, visible=args.visible,
                            out=args.out, timeout=args.timeout, refresh=not args.no_refresh,
                            name_filter=args.filter, renderer=args.renderer)
    return _emit(summary, args.json)


def _emit(summary: dict, as_json: bool) -> int:
    print(json.dumps(summary, indent=2) if as_json else format_summary(summary))
    return 0 if summary["passed"] else 1


def cmd_check(args: argparse.Namespace) -> int:
    result = check_project(args.project, resources=args.resources, lint=args.lint,
                           include_addons=args.include_addons, refresh=not args.no_refresh,
                           sim_lint=args.sim_lint)
    print(json.dumps(result, indent=2) if args.json else format_check(result))
    return 0 if result["ok"] else 1


def cmd_refresh(args: argparse.Namespace) -> int:
    note = refresh_class_cache(find_godot(), resolve_project(args.project), force=True)
    print(note)
    return 0


def cmd_api(args: argparse.Namespace) -> int:
    if args.action == "build":
        index = api.ensure_index(rebuild=True)
        desc = "with descriptions" if index["has_descriptions"] else "signatures only"
        print(f"API index for Godot {index['tag']}: {len(index['classes'])} classes ({desc})")
    elif args.action == "show":
        print(api.show(" ".join(args.query)))
    else:
        print(api.format_hits(api.search(" ".join(args.query), args.limit)))
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    images = [(Path(p), Path(p).stem) for p in args.images]
    out = sheet.contact_sheet(images, Path(args.out), columns=args.columns)
    if out is None:
        raise GodotAgentError("Pillow is not installed")
    print(out)
    return 0


def _format_result(result: dict) -> str:
    """One validated asset: status, problems, notes, what was done, where to look."""
    lines = [f"{'OK  ' if result['ok'] else 'FAIL'}  {result['path']}"]
    lines += [f"      problem: {p}" for p in result.get("problems", [])]
    lines += [f"      note: {n}" for n in result.get("notes", [])]
    lines += [f"      step: {s}" for s in result.get("steps", [])]
    for anim in result.get("animations", []):
        lines.append(f"      animation: {anim['name']} ({anim['frames']} frames, {anim['fps']} fps"
                     f"{', loops' if anim['loop'] else ''})")
    if result.get("frames_resource"):
        lines.append(f"      SpriteFrames: res://{result['frames_resource']}")
    metrics = {k: v for k, v in result.get("metrics", {}).items() if k in (
        "size", "tris", "colors", "silhouette_contrast", "duration_s", "true_peak_db", "loudness_lufs", "rms_db",
        "leading_silence_ms")}
    if metrics:
        lines.append("      " + ", ".join(f"{k}={v}" for k, v in metrics.items()))
    if result.get("preview"):
        lines.append(f"      preview: {result['preview']}")
    return "\n".join(lines)


def cmd_asset(args: argparse.Namespace) -> int:
    from .assets import audio, pipeline

    def emit(data, text: str, ok: bool = True) -> int:
        print(json.dumps(data, indent=2, default=str) if getattr(args, "json", False) else text)
        return 0 if ok else 1

    if args.action == "init":
        result = pipeline.init_style(args.project, out=args.out, force=args.force)
        return emit(result, f"wrote {result['style']}: {result['rules']} rules, {result['palettes']} palettes. "
                            "Review and tighten it, then run: godot-agent asset check")
    if args.action == "check":
        report = pipeline.check_assets(args.project, args.paths or None, args.style, review=args.review)
        return emit(report, pipeline.format_check(report), report["failed"] == 0)
    if args.action == "conform":
        size = [int(v) for v in args.size.lower().split("x")] if args.size else None
        result = pipeline.conform(args.project, args.source, args.to, args.style, size=size, anchor=args.anchor,
                                  resample=args.resample, outline=args.outline, clean=args.clean,
                                  trim=not args.no_trim, import_into_godot=not args.no_import)
        return emit(result, _format_result(result), result["ok"])
    if args.action == "aseprite":
        result = pipeline.import_aseprite(args.project, args.source, args.to, args.style,
                                          import_into_godot=not args.no_import)
        return emit(result, _format_result(result), result["ok"])
    if args.action == "sfx":
        if args.preset == "list":
            print("presets: " + ", ".join(audio.PRESETS))
            return 0
        if not args.to:
            raise GodotAgentError("--to res://path/name.wav is required")
        result = pipeline.make_sfx(args.project, args.preset, args.to, seed=args.seed, variants=args.variants,
                                   peak_db=args.peak_db, mutate_amount=args.mutate, style_path=args.style,
                                   import_into_godot=not args.no_import)
        text = "\n".join(_format_result(r) + f"\n      seed: {r['seed']}" for r in result["results"])
        return emit(result, text, all(r["ok"] for r in result["results"]))
    if args.action == "mesh":
        from .assets import meshes
        if args.source == "list":
            print("presets: " + ", ".join(meshes.PRESETS))
            return 0
        if not args.to:
            raise GodotAgentError("--to res://path/name.glb is required")
        colors = dict(c.split("=", 1) for c in args.color or [])
        result = pipeline.make_mesh(args.project, args.source, args.to, seed=args.seed, variants=args.variants,
                                    colors=colors, style_path=args.style, review=args.review,
                                    import_into_godot=not args.no_import)
        text = "\n".join(_format_result(r) + f"\n      seed: {r['seed']}" for r in result["results"])
        if result.get("preview"):
            text += f"\n  review: {result['preview']}"
        return emit(result, text, all(r["ok"] for r in result["results"]))
    if args.action == "icon":
        frame = {"trim": args.frame, "background": args.frame_background} if args.frame else None
        size = args.size
        if frame and args.frame_edge:
            # Command-button look: the subject is cropped to fill it, so render at twice the size.
            frame.update({"edge": args.frame_edge, "size": size})
            if args.frame_foot:
                frame["foot"] = args.frame_foot
            size *= 2
        result = pipeline.make_icon(args.project, args.model, args.to, size=size, pitch=args.pitch, yaw=args.yaw,
                                    frame=frame, style_path=args.style, import_into_godot=not args.no_import)
        return emit(result, _format_result(result), result["ok"])
    if args.action == "blender":
        params = dict(p.split("=", 1) for p in args.param or [])
        result = pipeline.make_blender(args.project, args.script, args.to, seed=args.seed, params=params,
                                       style_path=args.style, review=args.review, import_into_godot=not args.no_import)
        text = _format_result(result)
        if result.get("preview"):
            text += f"\n  review: {result['preview']}"
        return emit(result, text, result["ok"])
    if args.action == "texture":
        from .assets import textures
        if args.preset == "list":
            print("presets: " + ", ".join(textures.PRESETS) + "\ncards (with a foliage material): "
                  + ", ".join(textures.CARDS))
            return 0
        if not args.to:
            raise GodotAgentError("--to res://textures/name.png is required")
        colors = dict(c.split("=", 1) for c in args.color or [])
        result = pipeline.make_texture(args.project, args.preset, args.to, size=args.size, seed=args.seed,
                                       colors=colors, style_path=args.style, import_into_godot=not args.no_import)
        return emit(result, _format_result(result), result["ok"])
    if args.action == "ui":
        from .assets import ui
        if args.preset == "list":
            print(f"presets: {', '.join(ui.PRESETS)}\nfills: {', '.join(ui.FILLS)}\ntrims: {', '.join(ui.TRIMS)}")
            return 0
        if not args.to:
            raise GodotAgentError("--to res://ui/frames/name is required")
        size = [int(v) for v in args.size.lower().split("x")]
        result = pipeline.make_ui(args.project, args.preset, args.to, size, fill=args.fill, trim=args.trim,
                                  fill_color=args.fill_color, border=args.border, seed=args.seed, spec=args.spec,
                                  import_into_godot=not args.no_import)
        text = (f"{'OK  ' if result['ok'] else 'FAIL'}  {result['path']}\n" +
                "\n".join(f"      wrote: {f}" for f in result.get("files", [])) +
                "".join(f"\n      problem: {p}" for p in result.get("problems", [])) +
                (f"\n      preview: {result['preview']}" if result.get("preview") else ""))
        return emit(result, text, result["ok"])
    if args.action == "review":
        print("\n".join(pipeline.review(args.project, args.paths or None, args.style, args.out)))
        return 0
    if args.action == "regen":
        result = pipeline.regen(args.project, args.path, args.style)
        results = result.get("results", [result])
        return emit(result, "\n".join(_format_result(r) for r in results), all(r["ok"] for r in results))
    return 2


def cmd_clip(args: argparse.Namespace) -> int:
    """Captures of a scenario run (its folder, or a run folder plus --scenario) -> GIF/WebP."""
    folder = Path(args.run).expanduser()
    if args.scenario:
        folder = folder / args.scenario
    frames = sorted((folder / "captures").glob("*.png"))
    if args.skip:
        frames = frames[args.skip:]
    if args.limit:
        frames = frames[: args.limit]
    if not frames:
        raise GodotAgentError(f"no captures in {folder / 'captures'}; capture with capture_every in the scenario")
    crop = tuple(int(v) for v in args.crop.split(",")) if args.crop else None
    print(sheet.clip(frames, Path(args.out).expanduser(), fps=args.fps, width=args.width, crop=crop))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="godot-agent", description=(
        "Run, test, see and look up Godot 4 projects. Nothing is installed into the "
        "project: a runtime script is injected with `godot -s`."))
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check the toolchain")
    p.add_argument("--set-godot", metavar="PATH", help="remember this Godot binary")
    p.add_argument("--print-godot", action="store_true", help="print the Godot binary that will be used")
    p.set_defaults(func=cmd_doctor)

    def run_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--headless", action="store_true", help="no rendering (fastest; captures skipped)")
        p.add_argument("--visible", action="store_true", help="real window at real speed, to watch it")
        p.add_argument("--timeout", type=int, default=180, help="wall-clock seconds per run (default 180)")
        p.add_argument("--no-refresh", action="store_true", help="skip the class-cache staleness check")
        p.add_argument("--renderer", choices=["forward_plus", "mobile", "gl_compatibility"],
                       help="override the project's renderer, e.g. gl_compatibility to see the web build's look")
        p.add_argument("--json", action="store_true", help="print the full summary as JSON")

    p = sub.add_parser("run", help="boot a scene for N frames: errors, telemetry, captures")
    p.add_argument("project")
    p.add_argument("--scene", help="res:// path (default: main scene)")
    p.add_argument("--frames", type=int, default=120)
    p.add_argument("--capture-at", help="comma-separated frames to capture (default: last frame)")
    p.add_argument("--capture-every", type=int, default=0)
    p.add_argument("--overlay", action="store_true", help="label grouped nodes in captures")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--watch", action="append", metavar="NAME=EXPR", help="expression to report (repeatable)")
    run_options(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("scenario", help="run scenario files in parallel and report evidence")
    p.add_argument("project")
    p.add_argument("paths", nargs="*", help="scenario .json files or folders (default: tests/scenarios)")
    p.add_argument("-j", "--jobs", type=int, default=4)
    p.add_argument("--filter", help="only scenarios whose file name contains this")
    p.add_argument("--inline", metavar="JSON", help="scenario given as a JSON string")
    p.add_argument("--determinism", action="store_true", help="run each twice and diff state and pixels")
    p.add_argument("--out", help="output folder (default: ~/.cache/godot-agent/runs/<project>/<time>)")
    run_options(p)
    p.set_defaults(func=cmd_scenario)

    p = sub.add_parser("check", help="load every script (and scene) and report errors")
    p.add_argument("project")
    p.add_argument("--resources", action="store_true", help="also load every .tscn/.tres")
    p.add_argument("--lint", action="store_true", help="also run gdlint")
    p.add_argument("--sim-lint", action="append", metavar="PATH",
                   help="also fail on non-deterministic tokens (floats, trig, engine RNG, time, nodes...) in the "
                        ".gd files under PATH, e.g. scripts/sim; '# sim-lint: allow <token> <reason>' excuses a line "
                        "(repeatable)")
    p.add_argument("--include-addons", action="store_true")
    p.add_argument("--no-refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("refresh", help="rebuild the project's class cache (godot --import)")
    p.add_argument("project")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("api", help="offline class reference for the installed Godot version")
    p.add_argument("action", choices=["search", "show", "build"])
    p.add_argument("query", nargs="*")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.set_defaults(func=cmd_api)

    asset = sub.add_parser("asset", help="asset pipeline: style rules, checks, conforming, imports, SFX")
    asset_sub = asset.add_subparsers(dest="action", required=True)

    def asset_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        p = asset_sub.add_parser(name, help=help_text)
        p.add_argument("project")
        p.add_argument("--style", help="style file (default: <project>/asset_style.json)")
        p.add_argument("--json", action="store_true")
        p.set_defaults(func=cmd_asset)
        return p

    p = asset_parser("init", "draft asset_style.json and palettes from the project's existing assets")
    p.add_argument("--out", help="write the style somewhere else (palettes go next to it)")
    p.add_argument("--force", action="store_true")
    p = asset_parser("check", "validate assets against the style: size, palette, alpha, contrast, loudness...")
    p.add_argument("paths", nargs="*", help="files or folders (default: whole project)")
    p.add_argument("--review", action="store_true", help="also write a review sheet, failures first")
    p = asset_parser("conform", "rewrite any image to satisfy the rule for its destination path")
    p.add_argument("source", help="input image (drawn, downloaded, generated)")
    p.add_argument("--to", required=True, help="destination, e.g. res://assets/icons/fire.png")
    p.add_argument("--size", help="WxH, overrides the rule's size")
    p.add_argument("--anchor", choices=["center", "bottom"], default="center", help="bottom for characters")
    p.add_argument("--resample", choices=["box", "nearest"], default="box")
    p.add_argument("--outline", nargs="?", const="auto", help="1 px outline; color hex or darkest palette color")
    p.add_argument("--clean", action="store_true", help="remove stray single pixels")
    p.add_argument("--no-trim", action="store_true")
    p.add_argument("--no-import", action="store_true", help="skip the Godot import step")
    p = asset_parser("aseprite", "import a .aseprite file: packed sheet + SpriteFrames (one animation per tag)")
    p.add_argument("source")
    p.add_argument("--to", required=True, help="destination without extension, e.g. res://assets/sprites/bat")
    p.add_argument("--no-import", action="store_true")
    p = asset_parser("sfx", "generate a sound effect from a seeded preset ('list' shows presets)")
    p.add_argument("preset")
    p.add_argument("--to", help="destination .wav, e.g. res://assets/audio/sfx/coin.wav")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--variants", type=int, default=1, help="write N files with seeds seed..seed+N-1")
    p.add_argument("--peak-db", type=float, default=-1.0)
    p.add_argument("--mutate", type=float, default=0.0, help="randomize parameters by +-this fraction")
    p.add_argument("--no-import", action="store_true")
    p = asset_parser("mesh", "build low-poly .glb models from a seeded preset or recipe .json ('list' shows presets)")
    p.add_argument("source", help="preset name or recipe .json")
    p.add_argument("--to", help="destination .glb, e.g. res://models/props/tree.glb")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--variants", type=int, default=1, help="write N files with seeds seed..seed+N-1")
    p.add_argument("--color", action="append", metavar="NAME=#HEX", help="override a recipe color (repeatable)")
    p.add_argument("--review", action="store_true", help="render turntable views and a true-scale lineup")
    p.add_argument("--no-import", action="store_true")
    p = asset_parser("icon", "render a 3D model to a transparent PNG icon at a 3/4 view")
    p.add_argument("model", help="model file, e.g. res://models/towers/arrow.glb")
    p.add_argument("--to", required=True, help="destination .png, e.g. res://ui/icons/arrow.png")
    p.add_argument("--size", type=int, default=64)
    p.add_argument("--pitch", type=float, default=30.0, help="degrees the camera looks down")
    p.add_argument("--yaw", type=float, default=35.0, help="degrees around the model")
    p.add_argument("--frame", choices=["gold", "iron", "bronze", "wood", "hue"],
                   help="paint a backdrop and bevelled border in this trim (command-card style); hue (with "
                        "--frame-edge) draws the border in the backdrop's own colours")
    p.add_argument("--frame-background", default="#2b3d5c", help="backdrop colour for --frame")
    p.add_argument("--frame-edge", help="with --frame: the backdrop glows from --frame-background to this colour "
                                        "at the rim, and the subject is cropped to fill the button, rim-lit and "
                                        "shadowed (a Reforged-style command icon)")
    p.add_argument("--frame-foot", type=float, default=0.0,
                   help="with --frame-edge: keep this fraction of the height clear of the subject at the bottom, "
                        "shaded, so a cost drawn there sits on shadow (e.g. 0.2)")
    p.add_argument("--no-import", action="store_true")
    p = asset_parser("blender", "build a .glb with a Blender Python script (helpers: godot_agent/blender/gab.py)")
    p.add_argument("script", help="build script, e.g. art_src/blender/pine.py")
    p.add_argument("--to", required=True, help="destination .glb, e.g. res://models/vegetation/pine.glb")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--param", action="append", metavar="NAME=VALUE", help="passed to the script as gab.params")
    p.add_argument("--review", action="store_true", help="render turntable views and a true-scale lineup")
    p.add_argument("--no-import", action="store_true")
    p = asset_parser("texture", "paint a seamless terrain texture or a foliage card ('list' shows presets)")
    p.add_argument("preset", help="grass, meadow, path, dirt, sand, mud, rock, cobble, foliage_pine, foliage_leaves, "
                                  "foliage_grass, foliage_fern, or list")
    p.add_argument("--to", help="destination .png, e.g. res://textures/grass.png")
    p.add_argument("--size", type=int, default=256)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--color", action="append", metavar="NAME=#HEX", help="override a preset colour (repeatable)")
    p.add_argument("--no-import", action="store_true")
    p = asset_parser("ui", "paint nine-slice UI frames + StyleBoxTexture .tres ('list' shows presets, fills, trims)")
    p.add_argument("preset", help="panel, bar, tooltip, button (4 states), slot, frame, console, or list")
    p.add_argument("--to", help="destination without extension, e.g. res://ui/frames/console (console: a folder)")
    p.add_argument("--spec", help="console: JSON spec of the pieces (sizes, wells, window, crest), e.g. "
                                  "art_src/ui/console.json; missing keys take the kit's defaults")
    p.add_argument("--size", default="96x96", help="WxH in pixels")
    p.add_argument("--fill", default="stone", help="stone, wood, leather, slate")
    p.add_argument("--fill-color", help="hex colour for the fill")
    p.add_argument("--trim", default="gold", help="gold, iron, bronze, wood")
    p.add_argument("--border", type=int, help="trim width in pixels")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--no-import", action="store_true")
    p = asset_parser("review", "review sheets: images on the game's background, audio as waveform + spectrogram, "
                               "models as turntables")
    p.add_argument("paths", nargs="*")
    p.add_argument("-o", "--out", help="output folder")
    p = asset_parser("regen", "rebuild an asset exactly as asset_manifest.json recorded it")
    p.add_argument("path")

    p = sub.add_parser("clip", help="turn a scenario's captures into an animated GIF or WebP")
    p.add_argument("run", help="scenario output folder (…/runs/<project>/<time>/<scenario>) or a run folder")
    p.add_argument("--scenario", help="scenario name inside a run folder")
    p.add_argument("-o", "--out", required=True, help="clip.gif or clip.webp")
    p.add_argument("--fps", type=float, default=15.0)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--crop", help="x0,y0,x1,y1 in capture pixels, e.g. to leave out the HUD")
    p.add_argument("--skip", type=int, default=0, help="drop the first N captures")
    p.add_argument("--limit", type=int, default=0, help="keep at most N captures")
    p.set_defaults(func=cmd_clip)

    p = sub.add_parser("sheet", help="combine images into one contact sheet")
    p.add_argument("images", nargs="+")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--columns", type=int)
    p.set_defaults(func=cmd_sheet)
    return parser


def _terminate(signum: int, _frame: object) -> None:
    """SIGTERM: stop the processes a topology started in their own sessions, then exit."""
    from .topology import kill_live
    kill_live()
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _terminate)
    try:
        return args.func(args)
    except GodotAgentError as exc:
        print(f"godot-agent: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
