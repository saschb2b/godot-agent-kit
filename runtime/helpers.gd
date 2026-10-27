## Base instance for scenario expressions.
##
## Every method here can be called by name inside an expression, for example
##   first("player").hp > 0
##   count("enemies") == 0
##   text("HUD/Message") == "Floor 2"
## Autoloads, `scene`, `frame` and stored variables are passed in as inputs
## by the runner; these helpers cover lookups that expressions cannot express.
extends RefCounted

var _tree: SceneTree


func _init(tree: SceneTree) -> void:
	_tree = tree


## Node by path. Paths starting with /root are absolute; anything else is
## relative to the current scene, falling back to a recursive name search.
func node(path: String) -> Node:
	if path.begins_with("/root"):
		return _tree.root.get_node_or_null(path)
	var scene := _tree.current_scene
	if scene == null:
		return null
	var found := scene.get_node_or_null(path)
	if found == null and not path.contains("/"):
		found = scene.find_child(path, true, false)
	return found


## A resource by path, e.g. resource("res://data/level_1.tres").duplicate().
func resource(path: String) -> Resource:
	return ResourceLoader.load(path)


func first(group_name: String) -> Node:
	for n in group(group_name):
		return n
	return null


## Live members of a group, skipping nodes already queued for deletion.
func group(group_name: String) -> Array[Node]:
	var result: Array[Node] = []
	for n in _tree.get_nodes_in_group(group_name):
		if is_instance_valid(n) and not n.is_queued_for_deletion():
			result.append(n)
	return result


func count(group_name: String) -> int:
	return group(group_name).size()


func has(obj: Object, property: String) -> bool:
	return obj != null and property in obj


## Expressions cannot assign, so setup steps use this instead.
func set_prop(obj: Object, property: String, value: Variant) -> bool:
	if obj == null or not property in obj:
		return false
	obj.set(property, value)
	return true


## Text of a Label, RichTextLabel, Button or LineEdit, by node or path.
func text(target: Variant) -> String:
	var n: Node = target if target is Node else node(str(target))
	if n == null:
		return ""
	if n is RichTextLabel:
		return (n as RichTextLabel).get_parsed_text()
	if "text" in n:
		return str(n.get("text"))
	return ""


## Every visible piece of UI text on screen, top to bottom. Lets an agent
## "read" the screen without a screenshot.
func visible_text() -> PackedStringArray:
	var out := PackedStringArray()
	_collect_text(_tree.root, out)
	return out


## True if any visible UI text contains the given substring.
func screen_has(substring: String) -> bool:
	for line in visible_text():
		if line.contains(substring):
			return true
	return false


func pressed(action: String) -> bool:
	return Input.is_action_pressed(action)


func scene_path() -> String:
	return _tree.current_scene.scene_file_path if _tree.current_scene else ""


func paused() -> bool:
	return _tree.paused


func _collect_text(n: Node, out: PackedStringArray) -> void:
	if out.size() >= 80:
		return
	if n is CanvasItem and not (n as CanvasItem).is_visible_in_tree():
		return
	if n is Label or n is RichTextLabel or n is Button or n is LineEdit:
		var value := text(n).strip_edges()
		if not value.is_empty():
			out.append(value)
	for child in n.get_children():
		_collect_text(child, out)
