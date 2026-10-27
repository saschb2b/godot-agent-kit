---
name: godot-agent
description: Verify Godot 4 game changes with evidence. Use when working in any Godot project (a folder with project.godot) to check scripts compile, boot a scene and look at it, test gameplay with deterministic input scenarios, inspect engine errors and performance, look up the exact Godot API for the installed version, or check, conform, import and generate game assets (sprites, palettes, Aseprite animations, sound effects) against the project's style. Triggers include "does it work", "test this", "take a screenshot", "why does the scene crash", "write a test", "check the Godot API for X", "add a sprite/sound", "does this art fit the style", and any GDScript or asset change that should be verified before calling it done.
---

# godot-agent

Runs any Godot 4 project deterministically (fixed 60 fps timestep, seeded RNG, private `user://`
per run) without modifying it, and returns evidence. Available as the `godot-agent` CLI and as the
`godot-agent` MCP server (tools `godot_run`, `godot_scenario`, `godot_check`, `godot_api_search`,
`godot_api_show`, `godot_image`). The MCP tools return captures as images; with the CLI, read the
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
`watch_every` (frames, writes watches.csv), `capture_every`, `observe_groups`, `observe_props`.

| Step | Meaning |
|---|---|
| `{"wait": 30}` | advance 30 frames (0.5 s) |
| `{"wait_until": "expr", "timeout": 600}` | poll each frame; fatal on timeout |
| `{"wait_signal": "Obj.signal", "timeout": 600}` | e.g. `"TurnManager.turn_ended"` |
| `{"tap": "action", "hold": 2, "after": 1}` | press an InputMap action, release, settle |
| `{"press": "action"}` / `{"release": "action"}` | hold across steps (modifiers, running) |
| `{"key": "Space"}` | physical key (`A`, `Up`, `Escape`, `F1`) |
| `{"click": [x, y]}` | mouse click in viewport pixels, the same coordinates as a capture |
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
`scene_path()`, `paused()`. Private members work (`first('enemies')._is_dying`).

## Writing scenarios that hold up

- Set up state through the game's own API (`eval`/`set`) instead of long input sequences.
- Wait on conditions (`wait_until`, `wait_signal`), not fixed frame counts, for anything animated.
  Turn-based games: wait for the turn phase to return to player input after each action.
- One behaviour per scenario, named for it. Put a capture before and after the interesting moment.
- A scenario that cannot fail proves nothing: check it fails when the behaviour is broken.
- `RandomNumberGenerator` instances that call `randomize()` ignore `seed`. If `--determinism` reports
  differences, give that system a seed the game can set.

## Reading results

Every run writes `~/.cache/godot-agent/runs/<project>/<time>/<scenario>/` with `result.json`
(failures, assertions, errors with `res://` file:line, observations, telemetry summary),
`captures/*.png`, `contact_sheet.png`, `telemetry.csv` (frame_ms, process_ms, nodes,
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
| Sound effects | `asset sfx <project> <preset> --to res://sfx/x.wav --seed 3 --variants 4` (`asset_sfx`); presets: pickup, laser, explosion, powerup, hit, jump, blip, door, step, magic, death |
| Look at assets | `asset review <project> [paths]` (`asset_review`): images on the game's background color, audio as waveform over spectrogram |
| Rebuild exactly | `asset regen <project> <path>` (`asset_regen`), from the recipe in `asset_manifest.json` |

Then look at new art in context: `godot-agent run` with a capture of the scene that uses it.

Image rule keys: `size`, `max_size`, `multiple_of`, `frame` (sheet grid), `max_colors`, `palette`
(+ `palette_tolerance`), `binary_alpha`, `max_orphan_pixels`, `min_silhouette_contrast` (against the
style's `background`), `max_baseline_jitter` (feet bobbing between frames), `import` (expected
`.import` params). Audio keys: `formats`, `sample_rates`, `channels`, `max_peak_db` (true peak),
`loudness_lufs` (clips of 0.4 s or more), `rms_db` (shorter clips), `max_duration_s`,
`min_duration_s`, `max_leading_silence_ms` (late-feeling sounds), `max_trailing_silence_ms`,
`loop` (checks the loop seam). Set a key to `null` in a more specific rule to lift it.

## Limits

- Captures need rendering: Xvfb is used automatically; `--headless` skips captures but is faster.
- `--visible` opens a real window at real speed so a person can watch.
- No image-generation model is wired in. Output from any generator goes through `asset conform`.
- Music is not generated, and nothing replaces a person listening: ask the user to judge feel.
- Live editor manipulation (scene building, interactive play) belongs to the separate `godot` MCP.
