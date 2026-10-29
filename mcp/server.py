# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.26,<2", "pillow>=10"]
# ///
"""godot-agent MCP server (stdio). Start with: uv run --script mcp/server.py

Thin wrapper over the godot_agent package: same engine as the CLI, but results
come back as text plus images, so the model sees captures directly.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP, Image  # noqa: E402

from godot_agent import api  # noqa: E402
from godot_agent.check import check_project, format_check  # noqa: E402
from godot_agent.env import GodotAgentError  # noqa: E402
from godot_agent.runner import format_summary, run_quick, run_scenarios  # noqa: E402
from godot_agent.assets import pipeline  # noqa: E402
from godot_agent.cli import _format_result  # noqa: E402

INSTRUCTIONS = """\
godot-agent runs Godot 4 projects deterministically (fixed 60 fps timestep, seeded RNG, private
user:// per run) without modifying them, and returns evidence: pass/fail, failed assertions with
actual values, engine errors with res:// locations, telemetry, observations of game state, and
captures as images.

Loop: godot_check after editing scripts -> godot_run to see a scene -> encode the behaviour you
changed as a scenario -> godot_scenario until it passes -> keep the scenario in tests/scenarios/.
Claim a change works only with evidence from these tools. Look up engine APIs with
godot_api_search / godot_api_show (exact signatures for the installed version) instead of memory.
Live editing of scenes and interactive play are the separate godot MCP's job.

Assets: the project's asset_style.json (draft one with asset_init) is the style contract.
asset_check after adding or changing art, models or audio; asset_conform to bring any image into
style; asset_aseprite_import after drawing with the aseprite MCP (load the project's palettes/*.gpl);
asset_mesh for low-poly 3D models from seeded recipes; asset_icon to render a model as a UI icon;
asset_ui for painted nine-slice panels, buttons and frames; asset_texture for seamless painted
terrain textures;
asset_sfx for sound effects; asset_review to look at images and models and to listen by looking
at audio.
"""

SCENARIO_REFERENCE = """\
Scenario JSON:
{
  "name": "player_takes_stairs", "scene": "res://levels/a.tscn" (default: main scene),
  "seed": 1, "timeout_frames": 3600, "fail_on_errors": true, "ignore_errors": ["regex"],
  "watch": {"hp": "first('player').hp"}, "watch_every": 0, "capture_every": 0,
  "observe_groups": ["player", "enemies"], "observe_props": ["hp", "grid_pos"],
  "time_scale": 1 (8 = eight times the game time per frame for node-based games; it stretches
                   each physics tick, so a game with its own fixed-step simulation uses:)
  "ticks_per_frame": 1 (the kit sets Engine.set_meta("godot_agent_ticks_per_frame", N) before the
                   scene loads; the game runs that many simulation ticks per frame:
                   for i in int(Engine.get_meta("godot_agent_ticks_per_frame", 1)): sim.step()),
  "max_fps": 0 (hold frames to N per wall-clock second, delta unchanged),
  "deterministic": true (false: determinism=True runs it once, uncompared),
  "steps": [ ...one key per step... ]
}
Steps:
  {"wait": 30}                                    frames
  {"wait_until": "expr", "timeout": 600}          fails (fatal) on timeout
  {"wait_signal": "TurnManager.turn_ended", "timeout": 600}
  {"tap": "move_right", "hold": 2, "after": 1}    InputMap action; also "press"/"release"
  {"key": "Space"} | {"click": [x, y]}            viewport pixels = capture coordinates
  {"click": "expr", "button": "right"}            expr gives a Vector3/Node3D (projected through
                                                  the camera), Node2D, Control (centre) or Vector2
  {"hover": [x, y] | "expr"}                      move the mouse only (previews, tooltips)
  {"eval": "expr", "as": "name"}                  call methods; store result as a variable
  {"set": "first('player').hp", "value": 1}       or "value_expr": "Vector2(3, 4)"
  {"assert": "expr", "msg": "...", "fatal": false}
  {"capture": "label", "overlay": true}           image + observation (overlay labels group nodes)
  {"observe": "label"}                            state only: groups, props, watches, UI text
  {"scene": "res://other.tscn"}  {"log": "text"}
  {"repeat": 10, "steps": [...], "until": "expr"}
  {"set_ticks_per_frame": 8}                      change the fast-forward hook mid-run (0 holds it)
Expressions are Godot Expression syntax. Inputs: every autoload by name, engine singletons
(ResourceLoader, Input, Time, ...), scene (current scene), frame, vars, stored variables.
Helpers: resource(path), node(path), first(group), group(name), count(group),
has(obj, prop), set_prop(obj, prop, v), text(node_or_path), visible_text(), screen_has(s),
pressed(action), scene_path(), paused(), screen_pos(node_or_vec3), world_at(x, y, height),
camera(). Expressions cannot hold lambdas; put loops in a helper script and call it, e.g.
{"eval": "resource('res://tests/bot.gd').new()", "as": "bot"} then {"eval": "bot.step(scene)"}.

Several processes (a server plus headless Godot clients of the project, merged into one result):
  "topology": {
    "server": {"cmd": ["{python}", "server/main.py", "--port", "0"], "cwd": ".", "env": {},
               "ready": "listening.*port\\D*(?P<port>\\d+)"},   regex on its output; group port -> {port}
    "clients": [{"args": ["--", "--join=127.0.0.1:{port}"]},        engine options, --, the game's args
                {"args": [...], "scenario": "client2_steps.json"}], own scenario or steps file (or "steps")
    "timeout": 180,   one global limit in seconds; every process (and its children) is then stopped
    "max_fps": 60     clients are paced to wall-clock time (0: as fast as possible)
  },
  "compare_hashes": true   clients print "HASH <tick> <hex>" to stdout; every tick two or more hashed
                           must agree, else the first differing tick is reported
Placeholders in the server command and client args and steps: {port} {client} {clients} {out}
{project} {python}. Clients run headless (no captures). determinism=True skips a topology unless it
sets "deterministic": true; then both runs' clients and hash streams are compared.
"""

mcp = FastMCP("godot-agent", instructions=INSTRUCTIONS)


def _image(path: str, max_width: int = 1400) -> Image:
    from PIL import Image as PILImage

    with PILImage.open(path) as img:
        if img.width > max_width:
            img = img.resize((max_width, round(img.height * max_width / img.width)), PILImage.LANCZOS)
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, format="PNG", optimize=True)
    return Image(data=buffer.getvalue(), format="png")


def _with_images(text: str, summary: dict, images: bool, limit: int = 3) -> list:
    content: list = [text]
    if images:
        # Failing scenarios first: that is where the agent needs to look.
        ordered = sorted(summary["scenarios"], key=lambda r: r["passed"])
        for record in [r for r in ordered if r.get("contact_sheet")][:limit]:
            content += [f"{record['name']} ({'PASS' if record['passed'] else 'FAIL'}):", _image(record["contact_sheet"])]
    return content


def _guard(fn):
    try:
        return fn()
    except GodotAgentError as exc:
        return f"error: {exc}"


@mcp.tool()
def godot_run(project: str, scene: str | None = None, frames: int = 120, capture_at: list[int] | None = None,
              overlay: bool = False, seed: int = 0, watch: dict[str, str] | None = None,
              headless: bool = False, images: bool = True) -> list:
    """Boot a scene for N frames (60 per second) and return engine errors, telemetry, watch values
    and captures. Captures default to the last frame; capture_at=[1, 60, 120] gives a sequence.
    watch maps names to expressions, e.g. {"hp": "first('player').hp"}. overlay labels nodes by group.
    project is the folder containing project.godot."""
    return _guard(lambda: _with_images(format_summary(summary := run_quick(
        project, scene=scene, frames=frames, capture_at=capture_at, overlay=overlay, seed=seed,
        watch=watch, headless=headless)), summary, images))


SCENARIO_DESCRIPTION = """Run scenario files (paths: files or folders; default tests/scenarios) and/or one inline
scenario object, in parallel. Returns pass/fail per scenario with the failing assertion's
actual values, engine errors, and contact sheets of captures. determinism=True runs each twice
and diffs state and pixels.

""" + SCENARIO_REFERENCE


@mcp.tool(description=SCENARIO_DESCRIPTION)
def godot_scenario(project: str, paths: list[str] | None = None, scenario: dict | None = None,
                   jobs: int = 4, determinism: bool = False, headless: bool = False,
                   name_filter: str | None = None, images: bool = True) -> list:
    return _guard(lambda: _with_images(format_summary(summary := run_scenarios(
        project, paths=paths, inline=scenario, jobs=jobs, determinism=determinism, headless=headless,
        name_filter=name_filter)), summary, images))


@mcp.tool()
def godot_check(project: str, resources: bool = False, lint: bool = False,
                sim_lint: list[str] | None = None) -> str:
    """Load every script in the project (resources=True: every .tscn/.tres too) in one headless
    process and list parse errors and broken references with res:// file:line. Refreshes a stale
    class cache first, which fixes 'Could not find type' errors for class_name scripts created
    outside the editor. lint=True adds gdlint style issues. sim_lint=["scripts/sim"] also fails on
    tokens that break a lockstep simulation's determinism in those folders: floats and float
    literals, trig and other transcendental functions, lerp, engine RNGs, float vector and transform
    types, the scene tree, Time./OS./Engine./delta/time_scale, float rounding, hash(), Node types,
    .sort()/sort_custom/get_instance_id, await/call_deferred, and iterating a dictionary. Strings and
    comments are skipped; '# sim-lint: allow <token> <reason>' excuses one line."""
    return _guard(lambda: format_check(check_project(project, resources=resources, lint=lint,
                                                     sim_lint=sim_lint)))


@mcp.tool()
def godot_api_search(query: str, limit: int = 15) -> str:
    """Search the class reference of the installed Godot version: classes, methods, properties,
    signals, constants. Words must all match, e.g. 'tween property', 'Input parse_input_event'."""
    return _guard(lambda: api.format_hits(api.search(query, limit)))


@mcp.tool()
def godot_api_show(name: str) -> str:
    """Full reference for a class ('Tween') or member ('Tween.tween_property'), with exact
    signature and description for the installed Godot version. Walks base classes for members."""
    return _guard(lambda: api.show(name))


# ── Assets ──

def _asset_guard(fn):
    try:
        return fn()
    except (GodotAgentError, RuntimeError, ValueError) as exc:
        return [f"error: {exc}"]


@mcp.tool()
def asset_init(project: str, force: bool = False) -> list:
    """Draft <project>/asset_style.json and palettes/*.gpl from the project's existing assets: one
    rule per folder (size or frame grid, alpha, palette, silhouette contrast, audio levels), so new
    assets that break the pattern fail asset_check. Review and tighten the draft afterwards."""
    return _asset_guard(lambda: [json.dumps(pipeline.init_style(project, force=force))])


@mcp.tool()
def asset_check(project: str, paths: list[str] | None = None, review: bool = True) -> list:
    """Validate images, 3D models and audio against asset_style.json: size/frame grid, palette,
    color count, binary alpha, stray pixels, silhouette contrast on the game background, animation
    baseline jitter, Godot import settings; model triangles, size, origin, materials, vertex colors
    against the palette; true peak, loudness (LUFS/RMS), leading silence, loop seams.
    review=True also returns a sheet of the images and turntables of the models, failures first."""
    def run() -> list:
        report = pipeline.check_assets(project, paths, review=review)
        content: list = [pipeline.format_check(report)]
        for key in ("review_sheet", "model_review_sheet"):
            if report.get(key):
                content.append(_image(report[key]))
        return content
    return _asset_guard(run)


@mcp.tool()
def asset_conform(project: str, source: str, to: str, size: list[int] | None = None, anchor: str = "center",
                  outline: str | None = None, clean: bool = False, resample: str = "box") -> list:
    """Rewrite any image (drawn, downloaded, AI-generated) to satisfy the style rule for its
    destination `to` (res:// path): trim, scale (box filter, or nearest for pixel art), binary
    alpha, map to the palette, optional 1 px outline ("auto" = darkest palette color) and stray
    pixel cleanup. Records the recipe in asset_manifest.json, imports into Godot, and returns
    validation plus a before/after image. anchor="bottom" keeps characters standing on the floor."""
    def run() -> list:
        result = pipeline.conform(project, source, to, size=size, anchor=anchor, outline=outline,
                                  clean=clean, resample=resample)
        return [_format_result(result), _image(result["preview"], 800)]
    return _asset_guard(run)


@mcp.tool()
def asset_aseprite_import(project: str, source: str, to: str) -> list:
    """Import a .aseprite file: writes <to>.png (packed sheet) and <to>.tres (SpriteFrames with one
    animation per tag, frame durations and pingpong/reverse directions kept), imports into Godot
    and confirms Godot loads both. Pair with the aseprite MCP: draw with the project palette
    (palettes/*.gpl via load_palette), save, then import here."""
    return _asset_guard(lambda: [_format_result(pipeline.import_aseprite(project, source, to))])


@mcp.tool()
def asset_sfx(project: str, preset: str, to: str, seed: int = 1, variants: int = 1, mutate: float = 0.0,
              peak_db: float = -1.0) -> list:
    """Generate sound effects with a seeded chiptune synthesizer. Presets: pickup, laser, explosion,
    powerup, hit, jump, blip, door, step, magic, death, arrow, cannon, build, horn. variants=N writes N files (seeds seed..);
    mutate=0.1 nudges every parameter by up to 10%. Same preset and seed give the same file;
    the recipe is stored in asset_manifest.json. Returns validation and a waveform + spectrogram
    sheet, since sound has to be judged by looking."""
    def run() -> list:
        result = pipeline.make_sfx(project, preset, to, seed=seed, variants=variants, mutate_amount=mutate,
                                   peak_db=peak_db)
        text = "\n".join(_format_result(r) + f"\n      seed: {r['seed']}" for r in result["results"])
        sheets = pipeline.review(project, [r["path"] for r in result["results"]])
        return [text] + [_image(sheet, 800) for sheet in sheets]
    return _asset_guard(run)


@mcp.tool()
def asset_mesh(project: str, source: str, to: str, seed: int = 1, variants: int = 1,
               colors: dict[str, str] | None = None, review: bool = True) -> list:
    """Build low-poly 3D models (.glb) from a preset or a recipe .json: parts (box, cylinder, cone,
    sphere, rock, wedge) with sizes, transforms, named colors, jitter, repeats around a ring, chance;
    any number may be a [min, max] range drawn from the seed. Presets: tree_round, tree_pine, bush,
    rock, grass_tuft, flower, mushroom, stump, pumpkin, crystal. Colors resolve recipe < style rule
    "colors" < `colors`, so presets take the project palette. Writes the .glb plus a .import that
    shows its vertex colors, records the recipe for asset_regen, validates (triangles, size, origin,
    palette) and returns turntable views with a true-scale lineup. Recipe format: see the skill."""
    def run() -> list:
        result = pipeline.make_mesh(project, source, to, seed=seed, variants=variants, colors=colors, review=review)
        text = "\n".join(_format_result(r) + f"\n      seed: {r['seed']}" for r in result["results"])
        return [text] + ([_image(result["preview"], 1000)] if result.get("preview") else [])
    return _asset_guard(run)


@mcp.tool()
def asset_icon(project: str, model: str, to: str, size: int = 64, pitch: float = 30.0, yaw: float = 35.0,
               frame: str | None = None, frame_background: str = "#2b3d5c", frame_edge: str | None = None,
               frame_foot: float = 0.0) -> list:
    """Render a 3D model (.glb, or a res:// mesh or scene) to a PNG icon at a 3/4 view, for command
    cards, inventories and portraits. Transparent by default; frame="gold"|"iron"|"bronze"|"wood"
    paints a backdrop and a bevelled border like a strategy-game command button. frame_edge (a hex
    colour) gives the Reforged-style button instead: a glow from frame_background to frame_edge, the
    subject cropped to fill it, rim-lit and shadowed; with it, frame="hue" draws the border in the
    backdrop's own colours, and frame_foot (e.g. 0.2) keeps that fraction of the bottom clear and
    shaded for a cost. Uses the project's painted material, recorded for asset_regen, validated
    against the image rule for `to`."""
    def run() -> list:
        framed = {"trim": frame, "background": frame_background} if frame else None
        render = size
        if framed and frame_edge:
            framed.update({"edge": frame_edge, "size": size})
            if frame_foot:
                framed["foot"] = frame_foot
            render = size * 2
        result = pipeline.make_icon(project, model, to, size=render, pitch=pitch, yaw=yaw, frame=framed)
        return [_format_result(result), _image(result["preview"], 256)]
    return _asset_guard(run)


@mcp.tool()
def asset_blender(project: str, script: str, to: str, seed: int = 1, params: dict[str, str] | None = None,
                  review: bool = True) -> list:
    """Build a model with a Blender Python script, headless, and export it to `to` (.glb). The
    script starts from an empty scene and uses the kit's helpers (`import gab`; see the skill):
    smooth organic bodies (subdivide, displace_noise, smooth), vertex paint for the painted
    material (paint), foliage cards (bough, card, tint) for alpha-cut needle/leaf textures,
    emissive parts (glow), rigs and looping animations (armature, skin, animate). Front faces -Y in
    Blender. Materials map to the project's by name; recipe recorded for asset_regen; returns
    validation and turntable renders."""
    def run() -> list:
        result = pipeline.make_blender(project, script, to, seed=seed, params=params, review=review)
        return [_format_result(result)] + ([_image(result["preview"], 1000)] if result.get("preview") else [])
    return _asset_guard(run)


@mcp.tool()
def asset_texture(project: str, preset: str, to: str, size: int = 256, seed: int = 1,
                  colors: dict[str, str] | None = None) -> list:
    """Paint a seamless, hand-painted terrain texture: grass (blade strokes), meadow (with flowers),
    path and dirt (pebbles, cracks), sand, mud (wet sheen), rock and cobble (lit stone plates).
    Colours: preset names dark/mid/light/tip/pebble/lichen/flower_a.. overridable per call or by the
    style rule's "colors". Writes the PNG with a mipmapped .import; returns it tiled 2x2 so seams
    would show. Sample it in a terrain shader with world-space UVs."""
    def run() -> list:
        result = pipeline.make_texture(project, preset, to, size=size, seed=seed, colors=colors)
        return [_format_result(result), _image(result["preview"], 512)]
    return _asset_guard(run)


@mcp.tool()
def asset_ui(project: str, preset: str, to: str, size: list[int], fill: str = "stone", trim: str = "gold",
             fill_color: str | None = None, border: int | None = None, seed: int = 1,
             spec: str | None = None) -> list:
    """Paint nine-slice UI pieces and their StyleBoxTexture .tres: preset panel (gilded double trim,
    corner plates, studs), bar, tooltip, button (normal/hover/pressed/disabled files), slot
    (recessed), frame (border only, for portraits and minimaps). fill: stone, wood, leather, slate;
    trim: gold, iron, bronze, wood. The centre tiles seamlessly. `to` has no extension
    (res://ui/frames/console); use the .tres as a theme StyleBox. Preset console paints a whole
    strategy-game bottom console in one material (bronze over stone, recessed wells, an arched
    portrait window with one crest) into the folder `to`, from the JSON `spec` (e.g.
    art_src/ui/console.json), plus layout.json with the wells and windows for the HUD to load."""
    def run() -> list:
        result = pipeline.make_ui(project, preset, to, size, fill=fill, trim=trim, fill_color=fill_color,
                                  border=border, seed=seed, spec=spec)
        text = "\n".join([f"{'OK' if result['ok'] else 'FAIL'} {result['path']}"] + result.get("files", [])
                         + result.get("problems", []))
        return [text] + ([_image(result["preview"], 800)] if result.get("preview") else [])
    return _asset_guard(run)


@mcp.tool()
def asset_review(project: str, paths: list[str] | None = None) -> list:
    """Look at assets: images on the game's background color (pixel-crisp, pass/fail borders),
    3D models as turntables plus a true-scale lineup on a 1-unit grid, and audio as waveform over
    spectrogram (attack, decay and pitch movement are visible)."""
    return _asset_guard(lambda: [_image(sheet, 1000) for sheet in pipeline.review(project, paths)])


@mcp.tool()
def asset_regen(project: str, path: str) -> list:
    """Rebuild an asset exactly as asset_manifest.json recorded it (sfx, mesh, icon, conform,
    aseprite import)."""
    def run() -> list:
        result = pipeline.regen(project, path)
        return ["\n".join(_format_result(r) for r in result.get("results", [result]))]
    return _asset_guard(run)


@mcp.tool()
def godot_image(path: str, max_width: int = 1400) -> list:
    """Return an image file (a single capture, a contact sheet) so it can be looked at directly."""
    if not Path(path).is_file():
        return [f"error: no file at {path}"]
    return [path, _image(path, max_width)]


if __name__ == "__main__":
    mcp.run()
