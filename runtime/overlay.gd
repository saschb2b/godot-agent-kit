## Debug overlay drawn only on frames the runner captures with overlay enabled.
##
## Marks every node in the observed groups with a crosshair and its name, so a
## vision model can tie what it sees in the image to entries in the observation.
## 3D nodes are marked where the active camera projects their origin.
extends CanvasLayer

const COLORS := [
	Color("#ff4d6d"), Color("#4dd2ff"), Color("#ffd24d"), Color("#7dff4d"), Color("#c44dff")
]

var groups: PackedStringArray = []
var _canvas: Node2D


func _ready() -> void:
	layer = 128
	visible = false
	_canvas = Node2D.new()
	_canvas.draw.connect(_on_draw)
	add_child(_canvas)


func show_now() -> void:
	visible = true
	_canvas.queue_redraw()


func _on_draw() -> void:
	var font := ThemeDB.fallback_font
	for gi in groups.size():
		var color: Color = COLORS[gi % COLORS.size()]
		for n in get_tree().get_nodes_in_group(groups[gi]):
			var pos := Vector2.ZERO
			if n is Node3D:
				# Projected through the active camera; skip what the camera cannot see.
				var cam := get_viewport().get_camera_3d()
				var world := (n as Node3D).global_position
				if (
					cam == null
					or not (n as Node3D).is_visible_in_tree()
					or not cam.is_position_in_frustum(world)
				):
					continue
				pos = cam.unproject_position(world)
			elif n is CanvasItem and (n as CanvasItem).is_visible_in_tree():
				# Canvas transform included, so this is the on-screen position.
				pos = (n as CanvasItem).get_global_transform_with_canvas().origin
			else:
				continue
			_canvas.draw_line(pos + Vector2(-4, 0), pos + Vector2(4, 0), color, 1.0)
			_canvas.draw_line(pos + Vector2(0, -4), pos + Vector2(0, 4), color, 1.0)
			_canvas.draw_string_outline(
				font,
				pos + Vector2(5, -3),
				str(n.name),
				HORIZONTAL_ALIGNMENT_LEFT,
				-1,
				8,
				2,
				Color.BLACK
			)
			_canvas.draw_string(
				font, pos + Vector2(5, -3), str(n.name), HORIZONTAL_ALIGNMENT_LEFT, -1, 8, color
			)
