## 3D fixture: an angled camera over a ground plane with two crates. Records
## where clicks and hovers meet the ground, so scenarios can check 3D picking.
extends Node3D

var last_click := Vector3.INF
var last_button := 0
var last_hover := Vector3.INF


func _ready() -> void:
	var camera := Camera3D.new()
	camera.position = Vector3(0, 6, 6)
	add_child(camera)
	camera.look_at(Vector3.ZERO, Vector3.UP)
	camera.current = true
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, -30, 0)
	add_child(sun)
	var ground := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(12, 12)
	ground.mesh = plane
	add_child(ground)
	for i in 2:
		var crate := MeshInstance3D.new()
		crate.name = "Crate%d" % (i + 1)
		var box := BoxMesh.new()
		box.size = Vector3(0.8, 0.8, 0.8)
		crate.mesh = box
		crate.position = Vector3(-2.0 + 4.0 * i, 0.0, 1.0 - i)
		crate.add_to_group("crates")
		add_child(crate)


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and (event as InputEventMouseButton).pressed:
		last_click = _ground_at((event as InputEventMouseButton).position)
		last_button = (event as InputEventMouseButton).button_index
	elif event is InputEventMouseMotion:
		last_hover = _ground_at((event as InputEventMouseMotion).position)


func _ground_at(screen: Vector2) -> Vector3:
	var camera := get_viewport().get_camera_3d()
	var origin := camera.project_ray_origin(screen)
	var direction := camera.project_ray_normal(screen)
	return origin + direction * (-origin.y / direction.y)
