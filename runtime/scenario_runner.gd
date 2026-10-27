## Runs one scenario: loads a scene, executes steps, records evidence, quits.
##
## Evidence written to the output directory:
##   result.json     pass/fail, failures, assertions, errors, observations, telemetry summary
##   captures/*.png  viewport images (skipped in headless mode)
##   telemetry.csv   per-frame engine metrics
##   watches.csv     per-frame watch expressions (when watch_every > 0)
## The exit code is 0 when the scenario passed, 1 when it failed.
extends Node

const STEP_KINDS := [
	"wait",
	"wait_until",
	"wait_signal",
	"press",
	"release",
	"tap",
	"key",
	"click",
	"eval",
	"set",
	"scene",
	"assert",
	"capture",
	"observe",
	"log",
	"repeat"
]
## Properties reported for observed nodes when the scenario names none.
const DEFAULT_PROPS := ["grid_pos", "hp", "max_hp", "health", "state", "ai_state", "velocity"]
## frame_ms is wall-clock time between frames (includes rendering); process_ms
## and physics_ms are the engine's own per-frame timings.
const TELEMETRY_COLUMNS := [
	"frame",
	"frame_ms",
	"process_ms",
	"physics_ms",
	"nodes",
	"orphan_nodes",
	"objects",
	"draw_calls",
	"memory_mb"
]
const MAX_STEP_LOG := 400
const RESULT_MARKER := "[godot-agent] result written"

var _tree: SceneTree
var _scenario: Dictionary
var _out_dir: String
# Untyped on purpose: these hold script instances whose methods the
# static analyzer cannot see through their native base types.
var _logger
var _helpers
var _overlay
var _headless := false

var _frame := 0
var _timeout_frames := 3600
var _done := false
var _start_ms := 0
var _last_frame_usec := 0
var _current_step := ""
var _vars := {}

var _failures: Array[Dictionary] = []
var _assertions: Array[Dictionary] = []
var _captures: Array[Dictionary] = []
var _observations: Array[Dictionary] = []
var _step_log: Array[Dictionary] = []
var _expr_errors: Array[Dictionary] = []
var _telemetry: Array[PackedFloat64Array] = []
var _watch_rows: Array[Array] = []


func setup(
	tree: SceneTree, scenario: Dictionary, out_dir: String, logger: Logger, base_dir: String
) -> void:
	_tree = tree
	_scenario = scenario
	_out_dir = out_dir
	_logger = logger
	_helpers = load(base_dir.path_join("helpers.gd")).new(tree)
	_overlay = load(base_dir.path_join("overlay.gd")).new()
	_timeout_frames = int(scenario.get("timeout_frames", 3600))
	_headless = DisplayServer.get_name() == "headless"


func _ready() -> void:
	# Keep running while the game is paused so pause menus can be tested.
	process_mode = Node.PROCESS_MODE_ALWAYS
	add_child(_overlay)
	_start_ms = Time.get_ticks_msec()
	_run()


func _process(_delta: float) -> void:
	if _done:
		return
	_frame += 1
	_sample_telemetry()
	var capture_every := int(_scenario.get("capture_every", 0))
	if capture_every > 0 and _frame % capture_every == 0:
		_capture("auto_f%d" % _frame, bool(_scenario.get("overlay", false)))
	var watch_every := int(_scenario.get("watch_every", 0))
	if watch_every > 0 and _frame % watch_every == 0 and _scenario.has("watch"):
		_watch_rows.append([_frame, _eval_watches()])
	if _frame >= _timeout_frames:
		_observations.append(_observe("at_timeout"))
		_finish("timeout: scenario exceeded %d frames" % _timeout_frames)


# ── Flow ──


func _run() -> void:
	var scene_path: String = _scenario.get("scene", "")
	if scene_path.is_empty():
		scene_path = ProjectSettings.get_setting("application/run/main_scene", "")
	if not scene_path.is_empty():
		if not await _change_scene(scene_path):
			return
	await _run_steps(_scenario.get("steps", []), "")
	if not _done:
		_observations.append(_observe("final"))
		_finish("")


func _run_steps(steps: Array, prefix: String) -> void:
	for i in steps.size():
		if _done:
			return
		_current_step = prefix + str(i + 1)
		if not steps[i] is Dictionary:
			_fail(steps[i], "step must be a JSON object", true)
			return
		await _exec(steps[i])


func _exec(step: Dictionary) -> void:
	var kind := _kind_of(step)
	var step_id := _current_step
	var started := _frame
	match kind:
		"wait":
			await _frames(int(step["wait"]))
		"wait_until":
			await _wait_until(step)
		"wait_signal":
			await _wait_signal(step)
		"press":
			_action(str(step["press"]), true)
		"release":
			_action(str(step["release"]), false)
		"tap":
			_action(str(step["tap"]), true)
			await _frames(int(step.get("hold", 2)))
			_action(str(step["tap"]), false)
			await _frames(int(step.get("after", 1)))
		"key":
			_key(str(step["key"]), true)
			await _frames(int(step.get("hold", 2)))
			_key(str(step["key"]), false)
			await _frames(int(step.get("after", 1)))
		"click":
			await _click(step)
		"eval":
			var value: Variant = _eval(str(step["eval"]))
			if step.has("as"):
				_vars[str(step["as"])] = value
		"set":
			_set_step(step)
		"scene":
			await _change_scene(str(step["scene"]))
		"assert":
			_assert(step)
		"capture":
			await _capture(str(step["capture"]), bool(step.get("overlay", false)))
		"observe":
			_observations.append(_observe(str(step["observe"])))
		"log":
			print("[godot-agent] ", step["log"])
		"repeat":
			for n in int(step["repeat"]):
				if _done:
					return
				await _run_steps(step.get("steps", []), "%s.%d." % [step_id, n + 1])
				if step.has("until") and _truthy(_eval(str(step["until"]))):
					break
		_:
			_fail(step, "unknown step; use one of: %s" % ", ".join(STEP_KINDS), true)
	if _step_log.size() < MAX_STEP_LOG:
		_step_log.append(
			{"step": step_id, "kind": kind, "frame": started, "frames": _frame - started}
		)


func _kind_of(step: Dictionary) -> String:
	for kind: String in STEP_KINDS:
		if step.has(kind):
			return kind
	return ""


func _frames(count: int) -> void:
	for i in count:
		if _done:
			return
		await _tree.process_frame


# ── Steps ──


func _change_scene(path: String) -> bool:
	var previous := _tree.current_scene
	var err := _tree.change_scene_to_file(path)
	if err != OK:
		_fail({"scene": path}, "cannot load scene %s: %s" % [path, error_string(err)], true)
		return false
	# change_scene_to_file is deferred: wait until the new scene is in the tree and ready.
	var waited := 0
	while (
		_tree.current_scene == null
		or _tree.current_scene == previous
		or not _tree.current_scene.is_node_ready()
	):
		if _done:
			return false
		if waited > 600:
			_fail({"scene": path}, "scene %s never became ready" % path, true)
			return false
		await _frames(1)
		waited += 1
	# One more frame so work the scene deferred in _ready() has run.
	await _frames(1)
	return true


func _wait_until(step: Dictionary) -> void:
	var expr := str(step["wait_until"])
	var limit := int(step.get("timeout", 600))
	var waited := 0
	while not _truthy(_eval(expr)):
		if _done:
			return
		if waited >= limit:
			_fail(
				step,
				"wait_until timed out after %d frames: %s" % [limit, expr],
				bool(step.get("fatal", true))
			)
			return
		await _frames(1)
		waited += 1


## "wait_signal": "TurnManager.turn_ended" — everything before the last dot is
## an expression for the emitting object.
func _wait_signal(step: Dictionary) -> void:
	var spec := str(step["wait_signal"])
	var dot := spec.rfind(".")
	var target: Variant = _eval(spec.left(dot)) if dot > 0 else null
	var signal_name := spec.substr(dot + 1)
	if not target is Object or not (target as Object).has_signal(signal_name):
		_fail(step, "no signal '%s' on '%s'" % [signal_name, spec.left(dot)], true)
		return
	var arg_count := 0
	for info in (target as Object).get_signal_list():
		if info["name"] == signal_name:
			arg_count = info["args"].size()
	var fired := [false]
	# unbind() drops the signal's arguments so one callback fits any signal.
	var callback := (func() -> void: fired[0] = true).unbind(arg_count)
	(target as Object).connect(signal_name, callback, CONNECT_ONE_SHOT)
	var limit := int(step.get("timeout", 600))
	var waited := 0
	while not fired[0]:
		if _done:
			return
		if waited >= limit:
			if (target as Object).is_connected(signal_name, callback):
				(target as Object).disconnect(signal_name, callback)
			_fail(
				step,
				"signal %s not emitted within %d frames" % [spec, limit],
				bool(step.get("fatal", true))
			)
			return
		await _frames(1)
		waited += 1


func _action(action: String, is_pressed: bool) -> void:
	if not InputMap.has_action(action):
		var known := InputMap.get_actions().filter(
			func(a: StringName) -> bool: return not str(a).begins_with("ui_")
		)
		_fail(
			{"action": action},
			"unknown input action '%s'; project actions: %s" % [action, known],
			true
		)
		return
	var event := InputEventAction.new()
	event.action = action
	event.pressed = is_pressed
	event.strength = 1.0 if is_pressed else 0.0
	Input.parse_input_event(event)


func _key(key_name: String, is_pressed: bool) -> void:
	var code := OS.find_keycode_from_string(key_name)
	if code == KEY_NONE:
		_fail(
			{"key": key_name},
			"unknown key '%s' (use names like A, Space, Up, Escape, F1)" % key_name,
			true
		)
		return
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = is_pressed
	if is_pressed and key_name.length() == 1:
		event.unicode = key_name.to_lower().unicode_at(0)
	Input.parse_input_event(event)


## "click": [x, y] in viewport pixels (the coordinates of a capture image).
func _click(step: Dictionary) -> void:
	var point: Array = step["click"]
	var viewport_pos := Vector2(float(point[0]), float(point[1]))
	# Input events arrive in window coordinates; the root viewport may be stretched.
	var window_pos: Vector2 = _tree.root.get_final_transform() * viewport_pos
	var button := int(step.get("button", MOUSE_BUTTON_LEFT))
	var motion := InputEventMouseMotion.new()
	motion.position = window_pos
	motion.global_position = window_pos
	Input.parse_input_event(motion)
	await _frames(1)
	for is_pressed: bool in [true, false]:
		var event := InputEventMouseButton.new()
		event.position = window_pos
		event.global_position = window_pos
		event.button_index = button as MouseButton
		event.pressed = is_pressed
		event.button_mask = (1 << (button - 1)) if is_pressed else 0
		Input.parse_input_event(event)
		await _frames(int(step.get("hold", 1)) if is_pressed else int(step.get("after", 1)))


## "set": "first('player').hp", "value": 1 — or "value_expr": "Vector2i(3, 4)".
func _set_step(step: Dictionary) -> void:
	var spec := str(step["set"])
	var dot := spec.rfind(".")
	if dot <= 0:
		_fail(step, "set needs <object expression>.<property>", true)
		return
	var target: Variant = _eval(spec.left(dot))
	var property := spec.substr(dot + 1)
	var value: Variant = (
		_eval(str(step["value_expr"])) if step.has("value_expr") else step.get("value")
	)
	if not target is Object or not property in target:
		_fail(step, "cannot set '%s': no such object or property" % spec, true)
		return
	(target as Object).set(property, value)


func _assert(step: Dictionary) -> void:
	var expr := str(step["assert"])
	var ok := _truthy(_eval(expr))
	var record := {"step": _current_step, "frame": _frame, "expr": expr, "ok": ok}
	if step.has("msg"):
		record["msg"] = step["msg"]
	if not ok:
		record["actual"] = _explain(expr)
	_assertions.append(record)
	if not ok:
		var message := "assert failed: %s" % expr
		if record["actual"] != "":
			message += " (%s)" % record["actual"]
		if step.has("msg"):
			message += " — %s" % step["msg"]
		_fail(step, message, bool(step.get("fatal", false)))


## For "a == b" style asserts, report both sides so the agent sees the actual value.
func _explain(expr: String) -> String:
	var split := _split_comparison(expr)
	if split.is_empty():
		return "got %s" % var_to_str(_eval(expr)).left(200)
	var left: String = split[0]
	var right: String = split[1]
	return (
		"left %s = %s, right %s = %s"
		% [
			left.strip_edges(),
			var_to_str(_eval(left)).left(200),
			right.strip_edges(),
			var_to_str(_eval(right)).left(200)
		]
	)


## Finds the first comparison operator outside brackets and string literals.
func _split_comparison(expr: String) -> PackedStringArray:
	var depth := 0
	var quote := ""
	var i := 0
	while i < expr.length():
		var c := expr[i]
		if not quote.is_empty():
			if c == quote:
				quote = ""
		elif c == '"' or c == "'":
			quote = c
		elif c in "([{":
			depth += 1
		elif c in ")]}":
			depth -= 1
		elif depth == 0:
			for op: String in ["==", "!=", ">=", "<="]:
				if expr.substr(i, 2) == op:
					return PackedStringArray([expr.left(i), expr.substr(i + 2)])
			if c == ">" or c == "<":
				return PackedStringArray([expr.left(i), expr.substr(i + 1)])
		i += 1
	return PackedStringArray()


func _fail(step: Variant, message: String, fatal: bool) -> void:
	_failures.append(
		{
			"step": _current_step,
			"frame": _frame,
			"message": message,
			"fatal": fatal,
			"spec": _json(step)
		}
	)
	if fatal:
		_observations.append(_observe("at_failure"))
		_finish(message)


# ── Expressions ──


func _eval(expr: String) -> Variant:
	var names := PackedStringArray()
	var values: Array = []
	# Autoloads are root children other than the current scene and this runner.
	for child in _tree.root.get_children():
		if child == self or child == _tree.current_scene:
			continue
		names.append(str(child.name))
		values.append(child)
	# Engine singletons (ResourceLoader, Input, OS, Time, ProjectSettings, ...) are
	# not visible to Expression on their own.
	for singleton in Engine.get_singleton_list():
		if not singleton in names:
			names.append(singleton)
			values.append(Engine.get_singleton(singleton))
	names.append_array(["scene", "frame", "vars"])
	values.append_array([_tree.current_scene, _frame, _vars])
	for key: String in _vars:
		if key.is_valid_ascii_identifier() and not key in names:
			names.append(key)
			values.append(_vars[key])
	var expression := Expression.new()
	if expression.parse(expr, names) != OK:
		_expr_error(expr, expression.get_error_text())
		return null
	var result: Variant = expression.execute(values, _helpers, false)
	if expression.has_execute_failed():
		_expr_error(expr, expression.get_error_text())
		return null
	return result


func _expr_error(expr: String, message: String) -> void:
	for existing in _expr_errors:
		if existing["expr"] == expr and existing["error"] == message:
			existing["count"] += 1
			return
	if _expr_errors.size() < 100:
		_expr_errors.append(
			{"expr": expr, "error": message, "step": _current_step, "frame": _frame, "count": 1}
		)


func _truthy(value: Variant) -> bool:
	match typeof(value):
		TYPE_NIL:
			return false
		TYPE_BOOL:
			return value
		TYPE_INT, TYPE_FLOAT:
			return value != 0
		TYPE_STRING, TYPE_STRING_NAME:
			return not str(value).is_empty()
		TYPE_OBJECT:
			return is_instance_valid(value)
		TYPE_ARRAY, TYPE_DICTIONARY:
			return not value.is_empty()
	return true


func _eval_watches() -> Dictionary:
	var result := {}
	var watches: Dictionary = _scenario.get("watch", {})
	for watch_name: String in watches:
		result[watch_name] = _json(_eval(str(watches[watch_name])))
	return result


# ── Perception ──


func _capture(label: String, with_overlay: bool) -> void:
	var entry := {"label": label, "frame": _frame, "step": _current_step}
	if _headless:
		entry["skipped"] = "headless run; captures need rendering"
		_captures.append(entry)
		return
	if with_overlay:
		_overlay.groups = _observe_groups()
		_overlay.show_now()
	# Wait until this frame has been drawn, then read the viewport back.
	await RenderingServer.frame_post_draw
	var image := _tree.root.get_texture().get_image()
	_overlay.visible = false
	if image == null or image.is_empty():
		entry["skipped"] = "viewport returned no image"
	else:
		var file := "captures/%03d_%s.png" % [_captures.size(), label.validate_filename()]
		image.save_png(_out_dir.path_join(file))
		entry["path"] = file
		entry["size"] = [image.get_width(), image.get_height()]
	entry["observation"] = _observe(label)
	_captures.append(entry)


func _observe(label: String) -> Dictionary:
	var observation := {
		"label": label,
		"frame": _frame,
		"step": _current_step,
		"scene": _helpers.scene_path(),
		"paused": _tree.paused,
		"node_count": int(Performance.get_monitor(Performance.OBJECT_NODE_COUNT)),
		"watches": _eval_watches(),
		"ui_text": Array(_helpers.visible_text()),
		"groups": {},
	}
	var props: Array = _scenario.get("observe_props", DEFAULT_PROPS)
	for group_name in _observe_groups():
		var items: Array = []
		for n in _helpers.group(group_name):
			if items.size() >= 60:
				break
			items.append(_describe_node(n, props))
		observation["groups"][group_name] = items
	var focus := _tree.root.gui_get_focus_owner()
	if focus:
		observation["focus"] = str(focus.get_path())
	return observation


## Groups named by the scenario, or every non-internal group in the current scene.
func _observe_groups() -> PackedStringArray:
	if _scenario.has("observe_groups"):
		return PackedStringArray(_scenario["observe_groups"])
	var found := {}
	if _tree.current_scene:
		_collect_groups(_tree.current_scene, found, [0])
	var names := PackedStringArray(found.keys())
	names.sort()
	return names.slice(0, 12)


func _collect_groups(n: Node, found: Dictionary, visited: Array) -> void:
	visited[0] += 1
	if visited[0] > 3000:
		return
	for group_name in n.get_groups():
		if not str(group_name).begins_with("_"):
			found[str(group_name)] = true
	for child in n.get_children():
		_collect_groups(child, found, visited)


func _describe_node(n: Node, props: Array) -> Dictionary:
	var info := {"name": str(n.name), "class": n.get_class()}
	var scene := _tree.current_scene
	info["path"] = (
		str(scene.get_path_to(n)) if scene and scene.is_ancestor_of(n) else str(n.get_path())
	)
	var script := n.get_script() as Script
	if script:
		var global_name := str(script.get_global_name())
		info["script"] = (
			global_name if not global_name.is_empty() else script.resource_path.get_file()
		)
	if n is Node2D:
		info["pos"] = _json((n as Node2D).global_position.round())
	elif n is Node3D:
		info["pos"] = _json((n as Node3D).global_position.snappedf(0.01))
	if n is CanvasItem:
		info["visible"] = (n as CanvasItem).is_visible_in_tree()
	for prop: String in props:
		if prop in n:
			info[prop] = _json(n.get(prop))
	return info


## Converts any Variant into something JSON can hold, readable for an agent.
func _json(value: Variant, depth: int = 0) -> Variant:
	match typeof(value):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return value
		TYPE_FLOAT:
			return snappedf(value, 0.001)
		TYPE_STRING_NAME, TYPE_NODE_PATH:
			return str(value)
		TYPE_VECTOR2, TYPE_VECTOR2I:
			return [_json(value.x), _json(value.y)]
		TYPE_VECTOR3, TYPE_VECTOR3I:
			return [_json(value.x), _json(value.y), _json(value.z)]
		TYPE_COLOR:
			return "#" + (value as Color).to_html()
		TYPE_RECT2, TYPE_RECT2I:
			return [value.position.x, value.position.y, value.size.x, value.size.y]
		TYPE_OBJECT:
			if not is_instance_valid(value):
				return null
			if value is Node:
				return "<%s %s>" % [value.get_class(), value.name]
			if value is Resource:
				var res := value as Resource
				return (
					"<%s %s>"
					% [
						res.get_class(),
						res.resource_name if res.resource_name else res.resource_path
					]
				)
			return "<%s>" % value.get_class()
		TYPE_DICTIONARY:
			if depth > 3:
				return "{...}"
			var out := {}
			for key in value:
				out[str(key)] = _json(value[key], depth + 1)
			return out
	if value is Array or typeof(value) >= TYPE_PACKED_BYTE_ARRAY:
		if depth > 3:
			return "[...]"
		var items: Array = []
		for item in value:
			if items.size() >= 50:
				items.append("...")
				break
			items.append(_json(item, depth + 1))
		return items
	return var_to_str(value)


# ── Telemetry ──


func _sample_telemetry() -> void:
	var now := Time.get_ticks_usec()
	var frame_ms := (now - _last_frame_usec) / 1000.0 if _last_frame_usec > 0 else 0.0
	_last_frame_usec = now
	if _telemetry.size() >= 50000:
		return
	(
		_telemetry
		. append(
			PackedFloat64Array(
				[
					_frame,
					frame_ms,
					Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0,
					Performance.get_monitor(Performance.TIME_PHYSICS_PROCESS) * 1000.0,
					Performance.get_monitor(Performance.OBJECT_NODE_COUNT),
					Performance.get_monitor(Performance.OBJECT_ORPHAN_NODE_COUNT),
					Performance.get_monitor(Performance.OBJECT_COUNT),
					Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME),
					Performance.get_monitor(Performance.MEMORY_STATIC) / 1048576.0,
				]
			)
		)
	)


func _telemetry_summary() -> Dictionary:
	if _telemetry.is_empty():
		return {}
	var summary := {}
	for column in [1, 2, 3, 7]:
		var values: Array = _telemetry.map(
			func(row: PackedFloat64Array) -> float: return row[column]
		)
		values.sort()
		summary[TELEMETRY_COLUMNS[column]] = {
			"p50": snappedf(values[values.size() / 2], 0.01),
			"p95": snappedf(values[int(values.size() * 0.95)], 0.01),
			"max": snappedf(values[-1], 0.01),
		}
	var first: PackedFloat64Array = _telemetry[0]
	var last: PackedFloat64Array = _telemetry[-1]
	summary["nodes"] = {
		"start": int(first[4]),
		"end": int(last[4]),
		"max": int(_telemetry.map(func(row: PackedFloat64Array) -> float: return row[4]).max())
	}
	summary["orphan_nodes_end"] = int(last[5])
	summary["memory_mb_max"] = snappedf(
		_telemetry.map(func(row: PackedFloat64Array) -> float: return row[8]).max(), 0.1
	)
	return summary


# ── Results ──


func _finish(reason: String) -> void:
	if _done:
		return
	var final_watches := _eval_watches()
	_done = true
	var errors: Array = _logger.entries()
	var ignore: Array = _scenario.get("ignore_errors", [])
	var counted: Array = errors.filter(
		func(e: Dictionary) -> bool:
			return e["type"] != "warning" and not _matches_any(str(e["message"]), ignore)
	)
	var passed := reason.is_empty() and _failures.is_empty() and _expr_errors.is_empty()
	if bool(_scenario.get("fail_on_errors", true)) and not counted.is_empty():
		passed = false
	if reason.is_empty() and not passed:
		if not _failures.is_empty():
			reason = _failures[0]["message"]
		elif not _expr_errors.is_empty():
			reason = (
				"expression error: %s → %s" % [_expr_errors[0]["expr"], _expr_errors[0]["error"]]
			)
		else:
			reason = "engine error: %s" % counted[0]["message"]

	var result := {
		"name": _scenario.get("name", ""),
		"passed": passed,
		"reason": reason,
		"frames": _frame,
		"wall_ms": Time.get_ticks_msec() - _start_ms,
		"headless": _headless,
		"engine": Engine.get_version_info()["string"],
		"renderer": RenderingServer.get_video_adapter_name(),
		"seed": int(_scenario.get("seed", 0)),
		"failures": _failures,
		"assertions": _assertions,
		"expression_errors": _expr_errors,
		"errors": errors,
		"final_watches": final_watches,
		"captures": _captures,
		"observations": _observations,
		"steps": _step_log,
		"telemetry": _telemetry_summary(),
	}
	_write(_out_dir.path_join("result.json"), JSON.stringify(result, "  "))
	_write_telemetry()
	_write_watches()
	# Anything the engine logs after this line is shutdown noise (leak reports
	# at exit), which the CLI reports separately instead of failing the run.
	print(RESULT_MARKER)
	_tree.quit(0 if passed else 1)


func _matches_any(text: String, patterns: Array) -> bool:
	for pattern in patterns:
		var regex := RegEx.create_from_string(str(pattern))
		if regex and regex.search(text):
			return true
	return false


func _write_telemetry() -> void:
	var lines := PackedStringArray([",".join(TELEMETRY_COLUMNS)])
	for row in _telemetry:
		lines.append(
			",".join(Array(row).map(func(v: float) -> String: return str(snappedf(v, 0.001))))
		)
	_write(_out_dir.path_join("telemetry.csv"), "\n".join(lines))


func _write_watches() -> void:
	if _watch_rows.is_empty():
		return
	var names: Array = (_scenario.get("watch", {}) as Dictionary).keys()
	var lines := PackedStringArray([",".join(["frame"] + names)])
	for row in _watch_rows:
		var cells := [str(row[0])]
		for watch_name in names:
			cells.append(JSON.stringify(row[1].get(watch_name)).replace(",", ";"))
		lines.append(",".join(cells))
	_write(_out_dir.path_join("watches.csv"), "\n".join(lines))


func _write(path: String, text: String) -> void:
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file:
		file.store_string(text)
