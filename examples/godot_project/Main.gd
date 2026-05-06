extends Control

# Smallest possible example of wiring godot-loop into a Godot 4 project.
#
# Run via:
#   godot-loop run e2e --extra --inspect-port=8765
#
# Then poke at the running game:
#   godot-loop inspect --endpoint /scene
#   godot-loop input mouse_button --x 480 --y 270
#
# This scene draws a single button.  Clicking it (real or via /input)
# updates a label so observers can see the click landed.

var launch_config: LoopLaunchConfig

@onready var label: Label = $Box/Label
@onready var button: Button = $Box/Button


func _ready() -> void:
	launch_config = LoopLaunchConfig.new()
	launch_config.apply_command_line_args(OS.get_cmdline_user_args())

	# Stand up the inspector if --inspect-port=N was passed.
	if launch_config.inspect_port > 0:
		var inspector := RuntimeInspectorServer.new()
		inspector.setup(launch_config.inspect_port)
		inspector.register_provider("/state", func() -> Dictionary:
			return {"clicks": _click_count, "label_text": label.text})
		add_child(inspector)

	button.pressed.connect(_on_button_pressed)

	# Tell godot-loop's e2e runner that bootstrap is finished.
	print("ready")

	# Optional screenshot + quit, driven by the addon flags.
	if launch_config.screenshot_after_ms > 0 and launch_config.screenshot_path != "":
		await get_tree().create_timer(launch_config.screenshot_after_ms / 1000.0).timeout
		_save_screenshot(launch_config.screenshot_path)
	if launch_config.exit_after_bootstrap:
		await get_tree().create_timer(0.5).timeout
		get_tree().quit()


var _click_count: int = 0


func _on_button_pressed() -> void:
	_click_count += 1
	label.text = "Clicked %d time(s)" % _click_count


func _save_screenshot(path: String) -> void:
	var image: Image = get_viewport().get_texture().get_image()
	if image == null or image.is_empty():
		push_warning("godot-loop example: no viewport image to save")
		return
	image.save_png(path)
