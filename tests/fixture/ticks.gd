## Fixture for the ticks_per_frame hook: steps a counter simulation as many ticks per physics
## frame as the kit asks for, the way a lockstep game fast-forwards.
extends Node

var at_ready := -1
var sim := CounterSim.new()


func _ready() -> void:
	at_ready = int(Engine.get_meta("godot_agent_ticks_per_frame", 1))


func _physics_process(_delta: float) -> void:
	for i in int(Engine.get_meta("godot_agent_ticks_per_frame", 1)):
		sim.step()
