## godot-agent boot script.
##
## Injected into any Godot 4.5+ project without modifying it:
##   godot --path <project> -s <kit>/runtime/boot.gd -- --ga-scenario=<file.json> --ga-out=<dir>
##
## A script passed with -s replaces the main loop. Because it extends
## SceneTree, the project's autoloads still load normally; we then add the
## scenario runner to the root and let it drive the game.
extends SceneTree

var _runner  # scenario_runner.gd instance


func _initialize() -> void:
	var args := _parse_user_args()
	var scenario_path: String = args.get("ga-scenario", "")
	var out_dir: String = args.get("ga-out", "")
	if scenario_path.is_empty() or out_dir.is_empty():
		printerr("godot-agent: --ga-scenario and --ga-out are required")
		quit(2)
		return

	var text := FileAccess.get_file_as_string(scenario_path)
	var scenario: Variant = JSON.parse_string(text)
	if not scenario is Dictionary:
		printerr("godot-agent: scenario is not a JSON object: %s" % scenario_path)
		quit(2)
		return

	DirAccess.make_dir_recursive_absolute(out_dir.path_join("captures"))

	var base_dir := (get_script() as Script).resource_path.get_base_dir()
	var logger_script := _load_script(base_dir.path_join("error_logger.gd"))
	var runner_script := _load_script(base_dir.path_join("scenario_runner.gd"))
	if logger_script == null or runner_script == null:
		quit(3)
		return
	var logger: Logger = logger_script.new()
	OS.add_logger(logger)

	# Seed the global RNG (randi, randf, shuffle, pick_random). RandomNumberGenerator
	# instances that call randomize() are not covered; see the determinism check.
	seed(int(scenario.get("seed", 0)))

	_runner = runner_script.new()
	_runner.name = "GodotAgent"
	_runner.setup(self, scenario, out_dir, logger, base_dir)
	root.add_child.call_deferred(_runner)


## Loads a runtime script, quitting instead of hanging when it fails to parse.
func _load_script(path: String) -> GDScript:
	var script := load(path) as GDScript
	if script == null or not script.can_instantiate():
		printerr("godot-agent: runtime script failed to load: %s" % path)
		return null
	return script


func _parse_user_args() -> Dictionary:
	var result := {}
	for arg in OS.get_cmdline_user_args():
		if not arg.begins_with("--"):
			continue
		var pair := arg.substr(2).split("=", true, 1)
		result[pair[0]] = pair[1] if pair.size() > 1 else "true"
	return result
