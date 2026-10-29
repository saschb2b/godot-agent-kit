## Fixture client for topology tests. Joins the echo server named by --join=host:port, sends a
## line and waits for it to come back, then steps a toy integer simulation
## (Engine.get_meta("godot_agent_ticks_per_frame", 1) ticks per frame) and prints
## "HASH <tick> <hex>" every 60 ticks. --diverge-at=T flips its state from tick T on, so its
## hashes differ from every other client's from then.
extends Node

const LINE := "hello lantern\n"

var tick := 0
var echoed := false
var diverge_at := -1
var _state := 1
var _peer := StreamPeerTCP.new()
var _sent := false


func _ready() -> void:
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--join="):
			var address := arg.trim_prefix("--join=").rsplit(":", true, 1)
			_peer.connect_to_host(address[0], int(address[1]))
		elif arg.begins_with("--diverge-at="):
			diverge_at = int(arg.trim_prefix("--diverge-at="))


func _physics_process(_delta: float) -> void:
	if not echoed:
		_poll()
		return
	for i in int(Engine.get_meta("godot_agent_ticks_per_frame", 1)):
		_step()


func _poll() -> void:
	_peer.poll()
	if _peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		return
	if not _sent:
		_peer.put_data(LINE.to_utf8_buffer())
		_sent = true
	if _peer.get_available_bytes() >= LINE.length():
		echoed = _peer.get_utf8_string(LINE.length()) == LINE


func _step() -> void:
	tick += 1
	_state = (_state * 1103515245 + 12345) & 0x7fffffff
	if diverge_at >= 0 and tick >= diverge_at:
		_state ^= 1
	if tick % 60 == 0:
		var context := HashingContext.new()
		context.start(HashingContext.HASH_SHA256)
		context.update(var_to_bytes([tick, _state]))
		print("HASH %d %s" % [tick, context.finish().slice(0, 8).hex_encode()])
