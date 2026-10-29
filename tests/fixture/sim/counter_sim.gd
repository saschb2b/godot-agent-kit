## An integer simulation that passes the kit's sim lint: no floats, no engine RNG, no nodes.
class_name CounterSim
extends RefCounted

var tick := 0
var total_mm := 0
var _rng := 7


func step() -> void:
	tick += 1
	_rng ^= (_rng << 13) & 0xFFFFFFFF
	_rng ^= _rng >> 17
	_rng ^= (_rng << 5) & 0xFFFFFFFF
	total_mm += _rng % 1000


func state_hash() -> String:
	var context := HashingContext.new()
	context.start(HashingContext.HASH_SHA256)
	context.update(var_to_bytes([tick, total_mm, _rng]))
	return context.finish().slice(0, 8).hex_encode()
