extends SceneTree
## Passed as --script by check_mod.py so the engine starts without loading the game's main scene.

func _init() -> void:
	quit()
