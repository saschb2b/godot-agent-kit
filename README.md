# godot-agent-kit

Tools that let a coding agent verify Godot 4 game changes instead of guessing. It runs any
project deterministically, drives it with scripted input, and returns evidence: pass/fail,
assertion values, engine errors with `res://` locations, captures, game state and telemetry.
Nothing is installed into your projects. A runtime script is injected with `godot -s`, and the
project's autoloads load as usual.

| Piece | What it gives an agent |
|---|---|
| `godot-agent run` | Boot a scene for N frames; captures, errors, telemetry, watched values |
| `godot-agent scenario` | JSON input scripts with assertions, run in parallel, optional determinism diff |
| `godot-agent check` | Every script (and scene) loaded in one process; parse errors by file:line |
| `godot-agent api` | Offline class reference pinned to the installed Godot version |
| `godot-agent asset` | Style rules per asset folder, validation of images, 3D models and audio, conforming any image to the style, Aseprite import to SpriteFrames, low-poly 3D models from seeded recipes with painted surface patterns, rigged and animated models from Blender build scripts, alpha-cut foliage cards, UI icons rendered from models, painted nine-slice UI frames, seeded sound effects, review sheets and model turntables, reproducible rebuilds |
| MCP server `godot-agent` | The same, with captures returned as images |
| Skill `godot-agent` | When and how to use all of the above |

## Install

```bash
git clone https://github.com/saschb2b/godot-agent-kit.git
cd godot-agent-kit
./install.sh
```

The installer works in your home directory without sudo. It links the CLI into `~/.local/bin`,
installs gdtoolkit (gdlint and gdformat), builds the API index and installs the skill. System
packages need one manual step, which the installer prints when they are missing:
`sudo apt install xvfb ffmpeg` (off-screen captures, audio checks). `asset blender` needs
Blender 4.2 or newer on PATH (`sudo snap install blender --classic`). It also registers
these MCP servers with Claude Code (user scope): `godot-agent`, plus `godot` and `aseprite` when
`../godot-mcp` and `../aseprite-mcp` are built. Run `godot-agent doctor` to see what is in place.

## Use

```bash
godot-agent check ~/Documents/GitHub/my-game
godot-agent run ~/Documents/GitHub/my-game --scene res://levels/a.tscn --capture-at 1,60,120 --overlay
godot-agent scenario ~/Documents/GitHub/my-game            # tests/scenarios/*.json
godot-agent scenario ~/Documents/GitHub/my-game --determinism
godot-agent api search tween property
godot-agent api show Input.parse_input_event
```

The scenario format and agent workflow are documented in [skill/SKILL.md](skill/SKILL.md).
Runs are written to `~/.cache/godot-agent/runs/<project>/<time>/` (the last 30 are kept).

## Asset pipeline

```bash
godot-agent asset init ~/Documents/GitHub/my-game          # draft asset_style.json + palettes/*.gpl
godot-agent asset check ~/Documents/GitHub/my-game --review
godot-agent asset conform ~/Documents/GitHub/my-game drawing.png --to res://art/orb.png --outline
godot-agent asset aseprite ~/Documents/GitHub/my-game bat.aseprite --to res://art/bat
godot-agent asset sfx ~/Documents/GitHub/my-game pickup --to res://sfx/coin.wav --variants 4
godot-agent asset mesh ~/Documents/GitHub/my-game tree_round --to res://models/tree.glb --variants 3 --review
godot-agent asset mesh ~/Documents/GitHub/my-game art_src/tower.json --to res://models/tower.glb
godot-agent asset texture ~/Documents/GitHub/my-game foliage_pine --to res://textures/foliage_pine.png
godot-agent asset blender ~/Documents/GitHub/my-game art_src/blender/pine.py --to res://models/pine.glb --seed 2 --review
godot-agent asset icon ~/Documents/GitHub/my-game res://models/tower.glb --to res://ui/icons/tower.png --frame gold
godot-agent asset ui ~/Documents/GitHub/my-game panel --to res://ui/frames/console --size 256x192
godot-agent asset review ~/Documents/GitHub/my-game assets/audio models
```

`asset_style.json` holds rules matched by path glob. Every matching rule applies in order and
later keys win, the way CSS cascades. `init` drafts one rule per asset folder from what the files
already share: size or frame grid, alpha, palette, silhouette contrast and audio levels. New files
that break the pattern then fail `check`. Generators record their recipe in `asset_manifest.json`
(preset and seed, mesh recipe, Blender script and params, icon view, conform options, Aseprite
source), so `asset regen`
rebuilds a file exactly. Models are written as glTF binary with vertex colors and a `.import` file
that makes Godot show those colors; `review` renders them as turntables and a true-scale lineup.
Rule keys are listed in [skill/SKILL.md](skill/SKILL.md#assets).

## Example

[Lantern Hollow](https://github.com/saschb2b/Tower-Defense) is a small tower defense built with the kit:
every model, texture, icon, console frame and sound comes from `godot-agent asset`, and its gameplay is
covered by scenarios (`tests/scenarios/`), including a scripted player that must win all eight waves.

## How it works

- `runtime/boot.gd` is passed to Godot with `-s`. Because it extends `SceneTree`, the project's
  autoloads still load. It seeds the RNG, installs an error logger and adds the scenario runner.
- `runtime/scenario_runner.gd` loads the scene, executes steps, evaluates expressions with
  Godot's `Expression` class, and writes `result.json`, captures and telemetry.
- The Python CLI starts one Godot process per scenario with `--fixed-fps 60 --disable-vsync` and a
  private `XDG_DATA_HOME`, so saves never touch your real `user://` and parallel runs cannot
  collide. Runs that capture images share one private Xvfb display; the rest run `--headless`.
- When scripts are newer than `.godot/global_script_class_cache.cfg`, the CLI runs
  `godot --headless --import` first. The import may create `.uid` files for new scripts, the
  same files the editor would create.

## Self-test

```bash
godot-agent scenario tests/fixture
```

`moves_and_clicks` and `clicks_in_3d` must pass. `expected_failure` must fail with an assertion and an
engine error.

```bash
python3 -m unittest tests/test_assets.py -v
```

Runs the asset pipeline against a temporary copy of the fixture. It conforms a large
anti-aliased drawing into a 16x16 palette sprite, flags a broken sprite, regenerates sound
effects byte for byte, imports an Aseprite file that Godot then loads, builds every mesh preset
and checks Godot shows a model's vertex colors, and renders an icon from a model.
