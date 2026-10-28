## Renders 3D models for review: a turntable per model and a lineup at true scale.
##
##   godot --path <project> -s model_review.gd -- --ga-list=<list.json> --ga-out=<dir>
##
## list.json:
##   {"models": [{"path": "/abs/tree.glb", "label": "tree"}], "size": 256, "views": 4,
##    "pitch": 45, "background": "#2b3a2f"}
## Writes <out>/<index>_<view>.png per model, <out>/lineup.png and <out>/info.json
## (bounds per model). glTF files are loaded with GLTFDocument, so they need no
## import; res:// meshes and scenes are loaded as resources.
##
## With "icon": true it writes one <out>/<index>_icon.png per model instead: a
## tight 3/4 view ("pitch", "yaw") on a transparent background, for UI icons.
extends SceneTree

var _list: Dictionary
var _out := ""
var _viewport: SubViewport
var _stage: Node3D
var _camera: Camera3D


func _initialize() -> void:
	var args := {}
	for arg in OS.get_cmdline_user_args():
		var pair := arg.trim_prefix("--").split("=", true, 1)
		args[pair[0]] = pair[1] if pair.size() > 1 else ""
	_out = args.get("ga-out", "")
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(args.get("ga-list", "")))
	if not parsed is Dictionary or _out.is_empty():
		printerr("godot-agent: model_review needs --ga-list=<json> and --ga-out=<dir>")
		quit(2)
		return
	_list = parsed
	DirAccess.make_dir_recursive_absolute(_out)
	_build_studio()
	_render_all()


func _build_studio() -> void:
	_viewport = SubViewport.new()
	_viewport.own_world_3d = true
	_viewport.msaa_3d = Viewport.MSAA_4X
	_viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	root.add_child(_viewport)

	var background := Color(str(_list.get("background", "#3a4a3c")))
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = background
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.8, 0.82, 0.9)
	env.ambient_light_energy = 0.55
	var world_env := WorldEnvironment.new()
	world_env.environment = env
	_viewport.add_child(world_env)

	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, -35, 0)
	sun.light_energy = 1.0
	sun.shadow_enabled = true
	_viewport.add_child(sun)

	# Ground with a 1-unit grid (fainter quarter lines), so size reads at a glance.
	var ground := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(400, 400)
	ground.mesh = plane
	var shader := Shader.new()
	shader.code = """
shader_type spatial;
render_mode cull_disabled;
uniform vec3 base_color;
void fragment() {
	vec3 world = (INV_VIEW_MATRIX * vec4(VERTEX, 1.0)).xyz;
	vec2 cell = abs(fract(world.xz - 0.5) - 0.5) / fwidth(world.xz);
	vec2 quarter = abs(fract(world.xz * 4.0 - 0.5) - 0.5) / fwidth(world.xz * 4.0);
	float major = 1.0 - min(min(cell.x, cell.y), 1.0);
	float minor = 1.0 - min(min(quarter.x, quarter.y), 1.0);
	ALBEDO = base_color * (1.0 + 0.35 * major + 0.1 * minor);
	ROUGHNESS = 1.0;
}
"""
	var material := ShaderMaterial.new()
	material.shader = shader
	material.set_shader_parameter("base_color", background.lightened(0.12))
	ground.material_override = material
	_viewport.add_child(ground)

	_stage = Node3D.new()
	_viewport.add_child(_stage)
	_camera = Camera3D.new()
	_camera.fov = 35.0
	_camera.current = true
	_viewport.add_child(_camera)


func _render_all() -> void:
	if bool(_list.get("icon", false)):
		await _render_icons()
		return
	var size := int(_list.get("size", 256))
	var views := int(_list.get("views", 4))
	var pitch := float(_list.get("pitch", 45.0))
	var info: Array = []
	var models: Array = _list.get("models", [])
	_viewport.size = Vector2i(size, size)
	await process_frame
	for i in models.size():
		var entry: Dictionary = models[i]
		_clear_stage()
		var model := _load_model(str(entry["path"]))
		if model == null:
			info.append({"path": entry["path"], "error": "could not load"})
			continue
		_stage.add_child(model)
		var box := _bounds(model)
		info.append(
			{
				"path": entry["path"],
				"aabb_position": [box.position.x, box.position.y, box.position.z],
				"aabb_size": [box.size.x, box.size.y, box.size.z],
			}
		)
		for v in views:
			_aim(box, pitch, -30.0 + 360.0 * v / views)
			await _capture("%s/%d_%d.png" % [_out, i, v])

	# Lineup: every model at true scale, labelled, in rows of up to six.
	_clear_stage()
	var per_row := 6
	var x := 0.0
	var z := 0.0
	var row_depth := 0.0
	var all_box := AABB()
	var placed_any := false
	for i in models.size():
		var model := _load_model(str(models[i]["path"]))
		if model == null:
			continue
		_stage.add_child(model)
		var box := _bounds(model)
		if i > 0 and i % per_row == 0:
			x = 0.0
			z += row_depth + 0.6
			row_depth = 0.0
		model.position = Vector3(x - box.position.x, 0, z - box.position.z)
		x += box.size.x + maxf(0.2, box.size.x * 0.3)
		row_depth = maxf(row_depth, box.size.z)
		var label := Label3D.new()
		label.text = str(models[i].get("label", i))
		label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		label.no_depth_test = true
		label.pixel_size = 0.004
		label.font_size = 40
		label.outline_size = 12
		label.position = Vector3(model.position.x + box.get_center().x, -0.1, z + box.size.z + 0.12)
		_stage.add_child(label)
		var placed := _bounds(model).expand(label.position)
		all_box = placed if not placed_any else all_box.merge(placed)
		placed_any = true
	if placed_any:
		var rows := ceili(models.size() / float(per_row))
		_viewport.size = Vector2i(size * clampi(models.size(), 2, 5), size * (1 if rows == 1 else 2))
		_aim(all_box, pitch, 0.0)
		await _capture("%s/lineup.png" % _out)
	var file := FileAccess.open("%s/info.json" % _out, FileAccess.WRITE)
	file.store_string(JSON.stringify({"models": info}))
	file.close()
	quit(0)


func _render_icons() -> void:
	var size := int(_list.get("size", 128))
	_viewport.size = Vector2i(size, size)
	_viewport.transparent_bg = true
	var world_env := _viewport.get_children().filter(func(n: Node) -> bool: return n is WorldEnvironment)
	(world_env[0] as WorldEnvironment).environment.background_mode = Environment.BG_CLEAR_COLOR
	for child in _viewport.get_children():
		if child is MeshInstance3D:
			(child as MeshInstance3D).visible = false  # the ground grid
		elif child is DirectionalLight3D:
			(child as DirectionalLight3D).shadow_enabled = false
			(child as DirectionalLight3D).rotation_degrees = Vector3(-40, 30, 0)
	await process_frame
	var models: Array = _list.get("models", [])
	for i in models.size():
		_clear_stage()
		var model := _load_model(str(models[i]["path"]))
		if model == null:
			continue
		_stage.add_child(model)
		_aim(_bounds(model), float(_list.get("pitch", 30.0)), float(_list.get("yaw", 35.0)), 0.03)
		await _capture("%s/%d_icon.png" % [_out, i])
	var file := FileAccess.open("%s/info.json" % _out, FileAccess.WRITE)
	file.store_string(JSON.stringify({"models": models}))
	file.close()
	quit(0)


func _load_model(path: String) -> Node3D:
	if path.get_extension().to_lower() in ["glb", "gltf"]:
		var doc := GLTFDocument.new()
		var state := GLTFState.new()
		if doc.append_from_file(path, state) != OK:
			printerr("godot-agent: cannot read %s" % path)
			return null
		var scene := doc.generate_scene(state) as Node3D
		_paint_vertex_colors(scene)
		return scene
	var resource := load(path)
	if resource is Mesh:
		var instance := MeshInstance3D.new()
		instance.mesh = resource
		return instance
	if resource is PackedScene:
		return (resource as PackedScene).instantiate() as Node3D
	printerr("godot-agent: %s is not a model" % path)
	return null


## Godot ignores glTF vertex colors for albedo; an imported model gets them through
## the external "painted" material its .import file names, so do the same here:
## the project's painted material when the listing names one, else vertex colors.
func _paint_vertex_colors(scene: Node) -> void:
	var project_material: Material = null
	var material_path := str(_list.get("material", ""))
	if material_path != "" and material_path != "<null>" and ResourceLoader.exists(material_path):
		project_material = load(material_path)
	for mi: MeshInstance3D in scene.find_children("*", "MeshInstance3D", true, false):
		for s in mi.mesh.get_surface_count():
			var material := mi.get_active_material(s) as BaseMaterial3D
			if material == null or material.albedo_texture != null:
				continue
			var colors: Variant = mi.mesh.surface_get_arrays(s)[Mesh.ARRAY_COLOR]
			if colors == null or (colors as PackedColorArray).is_empty():
				continue
			if project_material and material.resource_name == "painted":
				mi.set_surface_override_material(s, project_material)
				continue
			var painted := material.duplicate() as BaseMaterial3D
			painted.vertex_color_use_as_albedo = true
			mi.set_surface_override_material(s, painted)


func _clear_stage() -> void:
	for child in _stage.get_children():
		_stage.remove_child(child)
		child.free()


## Bounds in stage space of every mesh under the node.
func _bounds(node: Node) -> AABB:
	var result := AABB()
	var first := true
	for mesh_instance in node.find_children("*", "MeshInstance3D", true, false) + [node]:
		if not mesh_instance is MeshInstance3D:
			continue
		var mi := mesh_instance as MeshInstance3D
		var box := mi.global_transform * mi.get_aabb()
		result = box if first else result.merge(box)
		first = false
	return result


## Looks down at pitch degrees and moves in until the box just fits the frame.
func _aim(box: AABB, pitch: float, yaw: float, margin := 0.06) -> void:
	var center := box.get_center()
	var direction := Vector3(0, 0, 1).rotated(Vector3.RIGHT, -deg_to_rad(pitch)).rotated(
		Vector3.UP, deg_to_rad(yaw)
	)
	var radius := maxf(box.size.length() * 0.5, 0.05)
	var near := radius * 0.5
	var far := radius * 40.0
	var size := Vector2(_viewport.size)
	# Binary search on distance: the closest camera that keeps all corners inside a margin.
	for i in 24:
		var distance := (near + far) * 0.5
		_place_camera(center, direction, distance, radius)
		var fits := true
		for c in 8:
			var corner := box.get_endpoint(c)
			var p := _camera.unproject_position(corner)
			if _camera.is_position_behind(corner) or p.x < size.x * margin or p.y < size.y * margin \
					or p.x > size.x * (1.0 - margin) or p.y > size.y * (1.0 - margin):
				fits = false
				break
		if fits:
			far = distance
		else:
			near = distance
	_place_camera(center, direction, far, radius)


func _place_camera(center: Vector3, direction: Vector3, distance: float, radius: float) -> void:
	_camera.position = center + direction * distance
	_camera.look_at(center, Vector3.UP)
	_camera.near = maxf(0.01, (distance - radius * 2.0) * 0.5)
	_camera.far = distance + radius * 4.0 + 50.0


func _capture(path: String) -> void:
	# Two frames: one to apply the new transforms and shadows, one to draw them.
	await process_frame
	await RenderingServer.frame_post_draw
	await RenderingServer.frame_post_draw
	var image := _viewport.get_texture().get_image()
	image.save_png(path)
