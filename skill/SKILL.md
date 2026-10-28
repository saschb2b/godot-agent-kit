---
name: godot-agent
description: Verify Godot 4 game changes with evidence. Use when working in any Godot project (a folder with project.godot) to check scripts compile, boot a scene and look at it, test gameplay with deterministic input scenarios (2D or 3D), inspect engine errors and performance, look up the exact Godot API for the installed version, or check, conform, import and generate game assets (sprites, palettes, Aseprite animations, low-poly 3D models, UI icons rendered from models, sound effects) against the project's style. Triggers include "does it work", "test this", "take a screenshot", "why does the scene crash", "write a test", "check the Godot API for X", "add a sprite/sound", "does this art fit the style", and any GDScript or asset change that should be verified before calling it done.
---

# godot-agent

Runs any Godot 4 project deterministically (fixed 60 fps timestep, seeded RNG, private `user://`
per run) without modifying it, and returns evidence. Available as the `godot-agent` CLI and as the
`godot-agent` MCP server (tools `godot_run`, `godot_scenario`, `godot_check`, `godot_api_search`,
`godot_api_show`, `godot_image`, and the `asset_*` tools). The MCP tools return captures as images; with the CLI, read the
`contact_sheet.png` path it prints.

## The rule

Do not say a game change works until a tool showed it: a passing scenario, a capture you looked
at, or a clean check. Report failures with the tool output.

## Loop

1. **Look up APIs instead of recalling them.** Godot 3 and 4.x APIs differ in ways that compile
   and then break. `godot-agent api search tween property`, `godot-agent api show Tween.tween_property`.
   Deprecated members are marked `[DEPRECATED: ...]`.
2. **After editing scripts:** `godot-agent check <project>` (add `--resources` after editing scenes).
   It also refreshes a stale class cache, the cause of "Could not find type X" for `class_name`
   scripts created outside the editor.
3. **See it:** `godot-agent run <project> --scene res://x.tscn --frames 120 --capture-at 1,60,120 --overlay`.
   Read the contact sheet. `--watch "hp=first('player').hp"` reports values.
4. **Prove the behaviour:** write a scenario for what you changed, run it until it passes, keep it in
   `<project>/tests/scenarios/` so it guards the behaviour from then on.
5. **Before finishing:** `godot-agent scenario <project>` runs every scenario in parallel.
   Use `--determinism` when touching RNG, spawning, physics or timing.

## Scenario format

```json
{
  "name": "player_steps_right",
  "scene": "res://levels/test_room.tscn",
  "seed": 1,
  "watch": {"x": "first('player').position.x"},
  "observe_groups": ["player", "enemies"],
  "steps": [
    {"capture": "start", "overlay": true},
    {"tap": "move_right"},
    {"wait_until": "TurnManager.current_phase == 0", "timeout": 120},
    {"assert": "first('player').position.x == 48", "msg": "moved one tile"},
    {"capture": "after_move"}
  ]
}
```

Top-level keys: `scene` (default main scene), `seed`, `timeout_frames` (3600), `fail_on_errors`
(true: any engine error fails the run), `ignore_errors` (regex list), `watch` (name → expression),
`watch_every` (frames, writes watches.csv), `capture_every`, `observe_groups`, `observe_props`,
`time_scale` (game seconds per real second; 8 plays a 5-minute match in under 3000 frames).

| Step | Meaning |
|---|---|
| `{"wait": 30}` | advance 30 frames (0.5 s) |
| `{"wait_until": "expr", "timeout": 600}` | poll each frame; fatal on timeout |
| `{"wait_signal": "Obj.signal", "timeout": 600}` | e.g. `"TurnManager.turn_ended"` |
| `{"tap": "action", "hold": 2, "after": 1}` | press an InputMap action, release, settle |
| `{"press": "action"}` / `{"release": "action"}` | hold across steps (modifiers, running) |
| `{"key": "Space"}` | physical key (`A`, `Up`, `Escape`, `F1`) |
| `{"click": [x, y]}` | mouse click in viewport pixels, the same coordinates as a capture |
| `{"click": "expr"}` | click what the expression gives: a `Vector3` or `Node3D` (projected through the active camera), a `Node2D`, a `Control` (its centre). `"button": "right"` |
| `{"hover": [x, y]}` or `{"hover": "expr"}` | move the mouse only: build previews, tooltips, hover states |
| `{"eval": "expr", "as": "name"}` | call methods; store the result as a variable |
| `{"set": "first('player').hp", "value": 1}` | assign; `"value_expr"` evaluates an expression |
| `{"assert": "expr", "msg": "...", "fatal": false}` | failures report both sides of `==`, `<`, ... |
| `{"capture": "label", "overlay": true}` | image + observation; overlay labels grouped nodes |
| `{"observe": "label"}` | state only: groups, props, watches, visible UI text |
| `{"scene": "res://x.tscn"}` | change scene and wait until ready |
| `{"repeat": 10, "steps": [...], "until": "expr"}` | loops, e.g. walk until a wall |

Expressions use Godot `Expression` syntax. Inputs: every autoload by name (`GameState.money`),
engine singletons (`ResourceLoader`, `Input`, `Time`, ...), `scene` (current scene root), `frame`,
`vars`, and stored variables. Helpers: `resource(path)`, `node(path)`,
`first(group)`, `group(name)`, `count(group)`, `has(obj, prop)`, `set_prop(obj, prop, value)`,
`text(node_or_path)`, `visible_text()`, `screen_has("Game Over")`, `pressed(action)`,
`scene_path()`, `paused()`, `screen_pos(node_or_vec3)` (capture pixel of a 3D point),
`world_at(x, y, height)` (ground point under a pixel), `camera()`. Private members work
(`first('enemies')._is_dying`). Expressions cannot contain lambdas (`func(c): ...`).

## Writing scenarios that hold up

- Set up state through the game's own API (`eval`/`set`) instead of long input sequences.
- Wait on conditions (`wait_until`, `wait_signal`), not fixed frame counts, for anything animated.
  Turn-based games: wait for the turn phase to return to player input after each action.
- One behaviour per scenario, named for it. Put a capture before and after the interesting moment.
- A scenario that cannot fail proves nothing: check it fails when the behaviour is broken.
- `RandomNumberGenerator` instances that call `randomize()` ignore `seed`. If `--determinism` reports
  differences, give that system a seed the game can set. Particles: `CPUParticles3D.use_fixed_seed`.
  Shaders: drive animation from a uniform the game advances, not `TIME`, so captures repeat.
- Loops and strategies belong in a helper script in the game's `tests/`, called from the scenario:
  `{"eval": "resource('res://tests/bot.gd').new()", "as": "bot"}`, then
  `{"repeat": 2000, "steps": [{"eval": "bot.step(scene)"}, {"wait": 15}], "until": "Game.state >= 2"}`.
  A scripted player that must win, plus an idle one that must lose, pins game balance.
- Long simulations: run them `--headless` with `time_scale`; a full match then takes under a second.
  Rendered runs use a software renderer (llvmpipe) and manage roughly 5 to 10 frames per second in
  3D, so keep captures to short, set-up moments (state set through `eval`/`set`, not played to).
- Headless runs keep no renderer data: `MultiMesh.get_instance_transform` and similar read back
  defaults. Assert on game state the game keeps itself.

## Reading results

Every run writes `~/.cache/godot-agent/runs/<project>/<time>/<scenario>/` with `result.json`
(failures, assertions, errors with `res://` file:line, observations, `vars` stored with `"as"`,
telemetry summary),
`captures/*.png` (turn them into a GIF with `godot-agent clip <scenario dir> -o clip.gif`, from a
scenario with `capture_every`), `contact_sheet.png`, `telemetry.csv` (frame_ms, process_ms, nodes,
orphan_nodes, draw_calls, memory) and `godot.log`. Rising `orphan_nodes` means leaked nodes.

## Assets

The style contract is `<project>/asset_style.json` (rules per path glob, cascading like CSS) plus
`palettes/*.gpl`. If a project has none, `godot-agent asset init <project>` drafts one from the
assets it already has. The draft records the status quo, so review and tighten it with the user.

| Task | Command (MCP tool) |
|---|---|
| Check art and audio after any change | `asset check <project> [paths] --review` (`asset_check`) |
| Bring any image into style (drawn, downloaded, generated) | `asset conform <project> in.png --to res://art/x.png [--anchor bottom] [--outline]` (`asset_conform`) |
| Pixel art you draw | aseprite MCP: `load_palette` with the project's `.gpl`, draw, `render_sprite` to look, save; then `asset aseprite <project> x.aseprite --to res://art/x` (`asset_aseprite_import`) gives a sheet plus a SpriteFrames `.tres`, one animation per tag |
| Low-poly 3D models | `asset mesh <project> <preset or recipe.json> --to res://models/x.glb --seed 3 --variants 4 [--color leaf=#d08a2e] --review` (`asset_mesh`); presets: tree_round, tree_pine, bush, rock, grass_tuft, flower, mushroom, stump, pumpkin, crystal |
| UI icons from models | `asset icon <project> res://models/x.glb --to res://ui/icons/x.png --size 64 [--frame gold --frame-background "#2b3d5c"]` (`asset_icon`): 3/4 view, transparent or as a framed command button |
| Painted terrain textures | `asset texture <project> grass --to res://textures/grass.png [--size 256 --color mid=#4f8a32]` (`asset_texture`): seamless, mipmapped; presets grass, meadow, path, dirt, sand, mud, rock, cobble. Sample with world-space UVs and blend by masks in a terrain shader; mix two scales to hide the repeat |
| Painted UI frames | `asset ui <project> panel --to res://ui/frames/console --size 256x192 --fill stone --trim gold` (`asset_ui`): nine-slice PNG plus StyleBoxTexture `.tres`; presets panel, bar, tooltip, button (four state files), slot, frame; fills stone, wood, leather, slate; trims gold, iron, bronze, wood |
| Sound effects | `asset sfx <project> <preset> --to res://sfx/x.wav --seed 3 --variants 4` (`asset_sfx`); presets: pickup, laser, explosion, powerup, hit, jump, blip, door, step, magic, death, arrow, cannon, build, horn |
| Look at assets | `asset review <project> [paths]` (`asset_review`): images on the game's background color, models as four-angle turntables plus a true-scale lineup on a 1-unit grid, audio as waveform over spectrogram |
| Rebuild exactly | `asset regen <project> <path>` (`asset_regen`), from the recipe in `asset_manifest.json` |

Then look at new art in context: `godot-agent run` with a capture of the scene that uses it.

### 3D model recipes

A recipe (keep it in the game, e.g. `art_src/models/tower.json`) is a tree of parts. Shapes:
`box` (`size`, `taper`, `taper_z`), `cylinder` (`radius`, `radius_top`, `height`, `sides`, `phase`),
`cone`, `sphere` (`radius`, `segments: [around, up]`, `lobes`, `lobe_depth`), `rock` (icosphere,
`detail` 0 or 1), `wedge` (triangular prism, `size`). Every part takes `at`, `rotate` (degrees: roll Z,
then pitch X, then yaw Y), `scale`, `color` (a name from `colors`, a hex, or a list to pick from),
`cap_color`, `color_top` (blends from `color` at the part's bottom to this at its top; `gradient`
shapes the blend: tips of trees lit, bases dark), `jitter` (vertex noise), `emit` (glow strength,
own material), `chance`, `bevel` (box and cylinder: chamfered edges that catch light), `serrate` and
`droop` (cone and cylinder bottom rim: every other vertex pulled in, the rest hung down, for pine
boughs and ragged cloth), and `pattern` / `cap_pattern` with
`pattern_scale` and `pattern_rotate`: plain, bricks, stone (large blocks), planks, shingles,
cobble, metal, gold, cloth, crystal, bark, leaves, thatch. Patterns are painted by the shared
`painted.gdshader` from UVs in metres, so a brick is the same size on every model. Groups:
`{"parts": [...]}` with a transform, `{"repeat": 5, "around": 0.3, "parts": [...]}` places copies on a
ring with local +Z facing out, so `rotate: [30, 0, 0]` leans each copy outward. Any number can be a
`[min, max]` range drawn from the seed. Top level: `colors`, `origin` (`bottom` default, `bottom-center`
also centres the footprint, `authored` keeps coordinates, for projectiles), `facet` (per-face
brightness variation, 0.06), `ground_ao` (darkening near y=0, 0.25). Models face +Z.

Colors resolve recipe, then the style rule's `colors` for the destination, then `--color`; give a
folder's rule named colors and every preset there takes the project palette. Godot ignores glTF
vertex colors for albedo, so `asset mesh` also writes `x.glb.import` mapping the model's `painted`
material to `res://materials/painted.tres`, a ShaderMaterial over `painted.gdshader` (both created
once; style key `model_material`; the kit refreshes the shader only while it still carries the kit
header). That shader is where the hand-painted look lives: per-brick and per-plank tints, lit
upper edges, dark grout, a sky-lit top and a soft rim. Tune its uniforms per project.

Scene-level look, beyond the assets: a low sun from the upper left (faces toward the camera lit,
long shadows falling away), SSAO plus soft contact shadows under units and props (a dark radial disc,
alpha-blended; the browser's Compatibility renderer has no SSAO and draws multiply-blended quads
black), a vignette, light depth fog, glow without bloom (bloom softens the whole frame). Check the look
with `--renderer gl_compatibility` too: the web build is WebGL 2, which also lacks depth of field.

What makes low poly read as hand-painted strategy-game art, in recipe terms: chunky proportions
(wide plinths, big overhanging roofs), `bevel` on every box and wall, a `pattern` on every large
surface, trims and bands in `gold`/`metal`, team-colour `cloth` banners, small emissive torches and
runes, and moving parts (flags, turrets, crystals) as separate models animated in code. A MultiMesh
needs the material on the mesh surface: copy `get_active_material(i)` onto the mesh when building one.

Model rule keys (`"model": {...}`): `max_tris`, `max_size` / `min_size` ([x, y, z], null skips an axis),
`origin` (`bottom`: lowest point on y=0; `bottom-center`: also footprint centred; `center`),
`origin_tolerance`, `max_materials`, `palette` + `palette_tolerance` (vertex colors must be a palette
color under some light: brightness may differ, hue may not), `colors` (named colors for recipes).
Style keys `model_background` and `model_material` tune review renders and the vertex-color material.

Image rule keys: `size`, `max_size`, `multiple_of`, `frame` (sheet grid), `max_colors`, `palette`
(+ `palette_tolerance`), `binary_alpha`, `max_orphan_pixels`, `min_silhouette_contrast` (against the
style's `background`), `max_baseline_jitter` (feet bobbing between frames), `import` (expected
`.import` params). Audio keys: `formats`, `sample_rates`, `channels`, `max_peak_db` (true peak),
`loudness_lufs` (clips of 0.4 s or more), `rms_db` (shorter clips), `max_duration_s`,
`min_duration_s`, `max_leading_silence_ms` (late-feeling sounds), `max_trailing_silence_ms`,
`loop` (checks the loop seam). Set a key to `null` in a more specific rule to lift it.

## Limits

- Captures need rendering: Xvfb is used automatically; `--headless` skips captures but is faster.
  3D renders through Vulkan on llvmpipe; it works (Forward+ included) but is slow.
- `--visible` opens a real window at real speed so a person can watch.
- No image-generation model is wired in. Output from any generator goes through `asset conform`.
- 3D models are procedural low poly from recipes; there is no sculpting, rigging or skeletal
  animation. Animate in code (bob, lean, scale) or bring rigged models from elsewhere.
- Music is not generated, and nothing replaces a person listening: ask the user to judge feel.
- Live editor manipulation (scene building, interactive play) belongs to the separate `godot` MCP.
