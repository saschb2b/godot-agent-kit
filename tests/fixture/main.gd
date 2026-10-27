## Fixture game: a square that steps 16 px per move action, a goal, an enemy,
## a score label and a button. Built in code so the fixture stays tiny.
extends Node2D

var player: Node2D
var score := 0
var clicks := 0
var label: Label


func _ready() -> void:
	player = _square("Player", Vector2(32, 96), Color.CORNFLOWER_BLUE)
	player.add_to_group("player")
	var enemy := _square("Slime", Vector2(160, 96), Color.LIME_GREEN)
	enemy.add_to_group("enemies")
	label = Label.new()
	label.name = "Score"
	label.position = Vector2(8, 8)
	label.text = "Score: 0"
	add_child(label)
	var button := Button.new()
	button.name = "Bump"
	button.text = "Bump"
	button.position = Vector2(240, 8)
	button.size = Vector2(64, 24)
	button.pressed.connect(func() -> void: clicks += 1)
	add_child(button)


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("move_right"):
		_step(Vector2.RIGHT)
	elif event.is_action_pressed("move_left"):
		_step(Vector2.LEFT)


func _step(direction: Vector2) -> void:
	player.position += direction * 16
	score += 1
	label.text = "Score: %d" % score


func trigger_error() -> void:
	push_error("fixture error on purpose")


func _square(square_name: String, pos: Vector2, color: Color) -> Node2D:
	var node := Node2D.new()
	node.name = square_name
	node.position = pos
	var rect := ColorRect.new()
	rect.color = color
	rect.size = Vector2(16, 16)
	rect.position = Vector2(-8, -8)
	node.add_child(rect)
	add_child(node)
	return node
