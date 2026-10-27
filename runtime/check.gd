## Loads every script (and optionally every scene and resource) in a project in
## one headless process, so parse errors and broken references all surface at once.
##   godot --headless --path <project> -s <kit>/runtime/check.gd -- [--ga-resources] [--ga-include-addons]
##   [--ga-only=res://a.tres,res://b.png]
## Prints one line "GA_CHECK {json}" with the files that failed to load.
extends SceneTree

const SCRIPT_EXTENSIONS := ["gd"]
const RESOURCE_EXTENSIONS := ["tscn", "scn", "tres", "res"]


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var include_resources := args.has("--ga-resources")
	var include_addons := args.has("--ga-include-addons")
	var files := PackedStringArray()
	# --ga-only=res://a.tres,res://b.png checks just those files.
	for arg in args:
		if arg.begins_with("--ga-only="):
			files = PackedStringArray(arg.trim_prefix("--ga-only=").split(",", false))
	if files.is_empty():
		_walk("res://", files, include_resources, include_addons)

	var failed := PackedStringArray()
	for path in files:
		var resource := ResourceLoader.load(path)
		if resource == null:
			failed.append(path)
		elif resource is GDScript:
			var script := resource as GDScript
			# Abstract classes cannot be instantiated by design; everything else
			# that cannot be instantiated failed to compile.
			if not script.can_instantiate() and not script.is_abstract():
				failed.append(path)
	print("GA_CHECK " + JSON.stringify({"checked": files.size(), "failed": failed}))
	quit(0 if failed.is_empty() else 1)


func _walk(
	dir_path: String, out: PackedStringArray, include_resources: bool, include_addons: bool
) -> void:
	var dir := DirAccess.open(dir_path)
	if dir == null:
		return
	for sub in dir.get_directories():
		if sub.begins_with("."):
			continue
		if sub == "addons" and dir_path == "res://" and not include_addons:
			continue
		_walk(dir_path.path_join(sub), out, include_resources, include_addons)
	for file in dir.get_files():
		var extension := file.get_extension()
		if (
			extension in SCRIPT_EXTENSIONS
			or (include_resources and extension in RESOURCE_EXTENSIONS)
		):
			out.append(dir_path.path_join(file))
