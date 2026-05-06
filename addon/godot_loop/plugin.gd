@tool
extends EditorPlugin

# This plugin currently has no editor-time behavior.  The two carry-over
# scripts (LoopLaunchConfig, RuntimeInspectorServer) are runtime-only — they
# are referenced by `class_name` from the consuming project's main scene.
# A plugin shell exists so projects that prefer Godot's plugin enable/disable
# UI can still toggle the addon, and so future editor utilities (smoke
# runner panel, screenshot diffing) have somewhere to land.

func _enter_tree() -> void:
	pass

func _exit_tree() -> void:
	pass
