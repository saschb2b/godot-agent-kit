## Collects engine and script errors with the frame they happened on.
##
## Godot can call loggers from any thread, so every access goes through a mutex.
## Never print from here: printing logs again and would recurse.
extends Logger

const TYPE_NAMES := ["error", "warning", "script", "shader"]

var _mutex := Mutex.new()
var _entries: Array[Dictionary] = []


func _log_error(
	function: String,
	file: String,
	line: int,
	code: String,
	rationale: String,
	_editor_notify: bool,
	error_type: int,
	script_backtraces: Array[ScriptBacktrace]
) -> void:
	var trace := PackedStringArray()
	for backtrace in script_backtraces:
		for i in backtrace.get_frame_count():
			# Frames from the injected runtime live outside res:// and are noise.
			if not backtrace.get_frame_file(i).begins_with("res://"):
				continue
			trace.append(
				(
					"%s:%d in %s()"
					% [
						backtrace.get_frame_file(i),
						backtrace.get_frame_line(i),
						backtrace.get_frame_function(i)
					]
				)
			)
	var entry := {
		"type": TYPE_NAMES[error_type] if error_type < TYPE_NAMES.size() else str(error_type),
		"message": rationale if not rationale.is_empty() else code,
		"file": file,
		"line": line,
		"function": function,
		"frame": Engine.get_process_frames(),
		"backtrace": trace,
	}
	# The innermost game frame is where an agent should look first.
	if not trace.is_empty():
		entry["at"] = trace[0]
	_mutex.lock()
	if _entries.size() < 500:
		_entries.append(entry)
	_mutex.unlock()


func entries() -> Array[Dictionary]:
	_mutex.lock()
	var copy: Array[Dictionary] = []
	copy.assign(_entries.duplicate(true))
	_mutex.unlock()
	return copy
