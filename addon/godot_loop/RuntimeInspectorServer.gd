extends Node
class_name RuntimeInspectorServer

# Localhost HTTP inspector for a running Godot client.  Opt-in via
# --inspect-port=N (or call setup() directly with a port) — when set, opens
# 127.0.0.1:N and serves these read-only built-in endpoints:
#
#   GET  /healthz                -> "ok"
#   GET  /scene?depth=N          -> {root: {name, type, path, visible, children: [...]}}
#                                    rooted at the SceneTree root (Window).
#                                    depth defaults to 32; depth=0 returns root only.
#   GET  /scene_tree?depth=N     -> same shape, rooted at get_tree().current_scene
#                                    so headless drivers see only the active screen.
#                                    depth defaults to 32; depth=0 returns root only.
#   GET  /text                   -> {items: [{path, type, text}, ...]}
#   GET  /viewport               -> root_size, root_position, content_scale, display info
#   GET  /screenshot.png         -> PNG of the current viewport
#   GET  /press_button?path=...  -> emit Button.pressed by NodePath (BaseButton).
#                                    Deterministic alternative to /input for
#                                    headless e2e (no focus juggling).
#   GET  /node_properties?path=...&names=a,b,c
#                                -> read live property values off a node by
#                                    NodePath. Use for inspecting autoload
#                                    state or any non-Control script property
#                                    at runtime. `names` is optional; if
#                                    omitted, returns the node's
#                                    script-exported properties.
#   POST /emit_signal            -> emit an arbitrary signal on a node. Body:
#                                    {"path": "...", "signal": "name", "args": [...]}.
#                                    Use when /press_button doesn't fit — e.g.
#                                    a custom signal on a non-BaseButton node.
#   POST /input                  -> inject an InputEvent (mouse_button|mouse_motion|key)
#
# After listen succeeds the inspector reparents itself to get_tree().get_root()
# so it survives change_scene_to_packed / change_scene_to_file calls — without
# this, swapping out the host scene would free the inspector mid-test.
#
# Projects can register additional GET endpoints via register_provider():
#
#   inspector.register_provider("/state", func() -> Dictionary:
#       return {"hp": player.hp, "mana": player.mana})
#
# The provider is a Callable returning a Dictionary; the inspector serializes
# it to JSON and serves it on the given path.
#
# Debug-only — bound to loopback.  Don't ship a build with it on by default.

const _MAX_REQUEST_BYTES: int = 16 * 1024
const _REQUEST_TIMEOUT_MS: int = 3000

var _server: TCPServer = null
var _connections: Array = []
var _port: int = 0
var _root_lookup: Callable = Callable()
var _providers: Dictionary = {}  # path -> Callable returning Dictionary


# port: TCP port to listen on (bound to 127.0.0.1).  <= 0 disables.
# root_lookup: optional Callable() -> Node, used as a fallback when no
# SceneTree root is reachable.  Most callers can leave it empty.
func setup(port: int, root_lookup: Callable = Callable()) -> void:
	_port = port
	_root_lookup = root_lookup


# Register a JSON GET endpoint.  Path must start with "/".
func register_provider(path: String, provider: Callable) -> void:
	_providers[path] = provider


func _ready() -> void:
	if _port <= 0:
		queue_free()
		return
	_server = TCPServer.new()
	var err: int = _server.listen(_port, "127.0.0.1")
	if err != OK:
		push_warning("RuntimeInspector: failed to listen on 127.0.0.1:%s (err=%s)" % [_port, err])
		_server = null
		queue_free()
		return
	print("runtime_inspector_listening: http://127.0.0.1:%s" % _port)
	set_process(true)
	# Reparent to the SceneTree root so the inspector survives scene swaps —
	# without this, when the host (e.g. Main.gd) gets freed by change_scene_*
	# the inspector dies with it and the port stops responding mid-test.
	# Deferred so we don't mutate the tree from inside the host's _ready.
	var tree := get_tree()
	if tree != null:
		var root := tree.get_root()
		if root != null and get_parent() != root:
			call_deferred("_reparent_to_root", root)


func _reparent_to_root(root: Node) -> void:
	var parent := get_parent()
	if parent == root or root == null:
		return
	if parent != null:
		parent.remove_child(self)
	root.add_child(self)


func _process(_delta: float) -> void:
	if _server == null:
		return
	while _server.is_connection_available():
		var peer: StreamPeerTCP = _server.take_connection()
		_connections.append({
			"peer": peer,
			"buffer": "",
			"started_at": Time.get_ticks_msec(),
		})
	for conn in _connections.duplicate():
		if not _service(conn):
			_connections.erase(conn)


# Returns true if the connection should remain in the pool for another
# pass; false when it's done (request served or peer dropped).
func _service(conn: Dictionary) -> bool:
	var peer: StreamPeerTCP = conn.peer
	peer.poll()
	if peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		return false
	var available: int = peer.get_available_bytes()
	if available > 0:
		var got: Array = peer.get_data(available)
		if got.size() == 2 and got[0] == OK:
			var bytes: PackedByteArray = got[1]
			conn.buffer += bytes.get_string_from_utf8()
	if conn.buffer.length() > _MAX_REQUEST_BYTES:
		_respond(peer, 413, "text/plain", "request too large")
		return false
	var split_idx: int = conn.buffer.find("\r\n\r\n")
	if split_idx < 0:
		if Time.get_ticks_msec() - int(conn.started_at) > _REQUEST_TIMEOUT_MS:
			peer.disconnect_from_host()
			return false
		return true
	var header_text: String = conn.buffer.substr(0, split_idx)
	var first_line: String = header_text.split("\r\n")[0]
	var parts: PackedStringArray = first_line.split(" ")
	if parts.size() < 2:
		_respond(peer, 400, "text/plain", "bad request")
		return false
	var method: String = parts[0]
	var path: String = parts[1]

	var body_text: String = ""
	if method == "POST" or method == "PUT":
		var content_length: int = _content_length_from_headers(header_text)
		var body_so_far: String = conn.buffer.substr(split_idx + 4)
		if body_so_far.length() < content_length:
			if Time.get_ticks_msec() - int(conn.started_at) > _REQUEST_TIMEOUT_MS:
				peer.disconnect_from_host()
				return false
			return true
		body_text = body_so_far.substr(0, content_length)

	if method == "GET":
		_handle_get(peer, path)
	elif method == "POST":
		match path:
			"/input":
				_handle_input_post(peer, body_text)
			"/emit_signal":
				_handle_emit_signal(peer, body_text)
			_:
				_respond(peer, 404, "text/plain", "not found")
	else:
		_respond(peer, 405, "text/plain", "method not allowed")
	return false


func _handle_get(peer: StreamPeerTCP, full_path: String) -> void:
	# Split route from query string so /press_button?path=... matches /press_button.
	var query_idx: int = full_path.find("?")
	var route: String = full_path if query_idx < 0 else full_path.substr(0, query_idx)
	var query_string: String = "" if query_idx < 0 else full_path.substr(query_idx + 1)

	if _providers.has(route):
		var provider: Callable = _providers[route]
		if provider.is_valid():
			var payload: Variant = provider.call()
			_respond_json(peer, payload)
			return
	match route:
		"/healthz":
			_respond(peer, 200, "text/plain", "ok\n")
		"/scene":
			_respond_json(peer, _scene_dump(_query_depth(query_string)))
		"/scene_tree":
			_respond_json(peer, _scene_tree_dump(_query_depth(query_string)))
		"/text":
			_respond_json(peer, _visible_text())
		"/screenshot.png":
			_respond_png(peer, _screenshot_bytes())
		"/viewport":
			_respond_json(peer, _viewport_info())
		"/press_button":
			_handle_press_button(peer, _query_get(query_string, "path"))
		"/node_properties":
			_handle_node_properties(peer, _query_get(query_string, "path"), _query_get(query_string, "names"))
		_:
			_respond(peer, 404, "text/plain", "not found")


const _DEFAULT_TREE_DEPTH := 32

func _query_depth(query_string: String) -> int:
	var raw: String = _query_get(query_string, "depth")
	if raw == "":
		return _DEFAULT_TREE_DEPTH
	if not raw.is_valid_int():
		return _DEFAULT_TREE_DEPTH
	var parsed: int = raw.to_int()
	if parsed < 0:
		return _DEFAULT_TREE_DEPTH
	return parsed


func _query_get(query_string: String, key: String) -> String:
	for pair in query_string.split("&"):
		var eq_idx: int = pair.find("=")
		if eq_idx < 0:
			continue
		var k: String = pair.substr(0, eq_idx)
		if k == key:
			return pair.substr(eq_idx + 1).uri_decode()
	return ""


# /press_button — emit Button.pressed by NodePath. The /input route can
# reach the same end-state but only when the button is focused; this is
# a deterministic alternative for headless e2e drivers.
func _handle_press_button(peer: StreamPeerTCP, node_path: String) -> void:
	if node_path == "":
		_respond(peer, 400, "application/json", JSON.stringify({"ok": false, "error": "missing path query parameter"}))
		return
	var root: Node = _root_node()
	if root == null:
		_respond(peer, 503, "application/json", JSON.stringify({"ok": false, "error": "no tree root"}))
		return
	var node: Node = root.get_node_or_null(NodePath(node_path))
	if node == null:
		_respond(peer, 404, "application/json", JSON.stringify({"ok": false, "error": "node not found", "path": node_path}))
		return
	if not (node is BaseButton):
		_respond(peer, 422, "application/json", JSON.stringify({"ok": false, "error": "not a Button (or BaseButton subclass)", "path": node_path, "type": node.get_class()}))
		return
	var btn: BaseButton = node
	btn.pressed.emit()
	_respond_json(peer, {"ok": true, "node": str(node.get_path()), "method": "signal", "type": node.get_class()})


# /node_properties — read live property values off a node by NodePath.
#
# Use when /scene_tree's per-node visibility/position dump isn't enough —
# e.g. inspecting an autoload singleton's current state, a panel's runtime
# text, or any @export var on a custom script.
#
# `names` is an optional comma-separated list. If empty, returns every
# script-exported property (var declared in the node's attached script).
# Values that aren't natively JSON-serializable (Vector2, Color, Object refs)
# are converted to dicts / strings via _serialize_value.
func _handle_node_properties(peer: StreamPeerTCP, node_path: String, names_csv: String) -> void:
	if node_path == "":
		_respond(peer, 400, "application/json", JSON.stringify({"ok": false, "error": "missing path query parameter"}))
		return
	var root: Node = _root_node()
	if root == null:
		_respond(peer, 503, "application/json", JSON.stringify({"ok": false, "error": "no tree root"}))
		return
	var node: Node = root.get_node_or_null(NodePath(node_path))
	if node == null:
		_respond(peer, 404, "application/json", JSON.stringify({"ok": false, "error": "node not found", "path": node_path}))
		return

	var names: Array = []
	if names_csv != "":
		for raw in names_csv.split(","):
			var trimmed: String = raw.strip_edges()
			if trimmed != "":
				names.append(trimmed)
	else:
		# Default: every script-exported property on the attached script.
		# (PROPERTY_USAGE_SCRIPT_VARIABLE catches @export and `var` declarations.)
		for prop in node.get_property_list():
			var usage: int = int(prop.get("usage", 0))
			if (usage & PROPERTY_USAGE_SCRIPT_VARIABLE) != 0:
				names.append(String(prop.get("name", "")))

	var props: Dictionary = {}
	var missing: Array = []
	for name in names:
		# Variant.IN absent in GDScript; check via get() and validate.
		# Use node.get() which returns null for non-existent props; distinguish
		# by checking the property list.
		var found: bool = false
		for prop in node.get_property_list():
			if String(prop.get("name", "")) == name:
				found = true
				break
		if not found:
			missing.append(name)
			continue
		props[name] = _serialize_value(node.get(name))

	var payload: Dictionary = {
		"ok": true,
		"path": str(node.get_path()),
		"type": node.get_class(),
		"properties": props,
	}
	if missing.size() > 0:
		payload["missing"] = missing
	_respond_json(peer, payload)


# /emit_signal — emit an arbitrary signal on a node.
#
# POST body: {"path": "...", "signal": "name", "args": [optional list]}.
# Validates the node exists, the signal exists on the node, and emits with
# the given args. Args must be JSON-native (string / number / bool / null /
# array / object); callers passing complex Godot types must serialize them
# themselves or rely on the receiver coercing.
func _handle_emit_signal(peer: StreamPeerTCP, body: String) -> void:
	var parsed: Variant = JSON.parse_string(body)
	if not (parsed is Dictionary):
		_respond(peer, 400, "application/json", JSON.stringify({"ok": false, "error": "body must be a JSON object"}))
		return
	var payload: Dictionary = parsed
	var node_path: String = "%s" % payload.get("path", "")
	var signal_name: String = "%s" % payload.get("signal", "")
	if node_path == "" or signal_name == "":
		_respond(peer, 400, "application/json", JSON.stringify({"ok": false, "error": "body requires 'path' and 'signal'"}))
		return
	var args_raw: Variant = payload.get("args", [])
	var args_array: Array = args_raw if args_raw is Array else []

	var root: Node = _root_node()
	if root == null:
		_respond(peer, 503, "application/json", JSON.stringify({"ok": false, "error": "no tree root"}))
		return
	var node: Node = root.get_node_or_null(NodePath(node_path))
	if node == null:
		_respond(peer, 404, "application/json", JSON.stringify({"ok": false, "error": "node not found", "path": node_path}))
		return
	if not node.has_signal(signal_name):
		_respond(peer, 422, "application/json", JSON.stringify({
			"ok": false,
			"error": "node has no such signal",
			"path": node_path,
			"signal": signal_name,
		}))
		return

	# Godot's emit_signal takes variadic args; callv expects an Array.
	var err: int = node.callv("emit_signal", [signal_name] + args_array)
	if err != OK:
		_respond(peer, 500, "application/json", JSON.stringify({
			"ok": false,
			"error": "emit_signal returned error",
			"code": err,
			"path": node_path,
			"signal": signal_name,
		}))
		return
	_respond_json(peer, {
		"ok": true,
		"path": str(node.get_path()),
		"signal": signal_name,
		"args_count": args_array.size(),
	})


# Convert a Variant to a JSON-friendly representation. Godot's JSON.stringify
# already handles primitives + Array + Dictionary; this adds Vector2/3/4,
# Color, Rect2, and falls back to str() for Object refs and unknown types.
func _serialize_value(v: Variant) -> Variant:
	if v is Vector2:
		return {"x": v.x, "y": v.y}
	if v is Vector2i:
		return {"x": v.x, "y": v.y}
	if v is Vector3:
		return {"x": v.x, "y": v.y, "z": v.z}
	if v is Vector3i:
		return {"x": v.x, "y": v.y, "z": v.z}
	if v is Vector4:
		return {"x": v.x, "y": v.y, "z": v.z, "w": v.w}
	if v is Color:
		return {"r": v.r, "g": v.g, "b": v.b, "a": v.a}
	if v is Rect2:
		return {"x": v.position.x, "y": v.position.y, "w": v.size.x, "h": v.size.y}
	if v is NodePath:
		return str(v)
	if v is StringName:
		return String(v)
	if v is Object:
		# Object refs aren't JSON-serializable; surface a string fingerprint.
		if v == null:
			return null
		return "<%s#%d>" % [v.get_class(), v.get_instance_id()]
	if v is Array:
		var out: Array = []
		for item in v:
			out.append(_serialize_value(item))
		return out
	if v is Dictionary:
		var out_dict: Dictionary = {}
		for k in v:
			out_dict[String(k)] = _serialize_value(v[k])
		return out_dict
	return v


# /scene_tree — like /scene but rooted at the *current scene* rather than
# the SceneTree root window, so test drivers see only the active screen.
func _scene_tree_dump(depth: int = _DEFAULT_TREE_DEPTH) -> Dictionary:
	var tree := get_tree()
	if tree == null:
		return {"available": false}
	var current: Node = tree.current_scene
	if current == null:
		return {"available": false}
	return {"available": true, "root": _node_to_dict(current, depth), "depth": depth}


func _content_length_from_headers(header_text: String) -> int:
	for line in header_text.split("\r\n"):
		var lower: String = line.to_lower()
		if lower.begins_with("content-length:"):
			return int(line.substr(line.find(":") + 1).strip_edges())
	return 0


# ---------------------------------------------------------------------------
# /input — inject an InputEvent into the running viewport.
# ---------------------------------------------------------------------------

const _MOUSE_BUTTON_LOOKUP: Dictionary = {
	"left": MOUSE_BUTTON_LEFT,
	"right": MOUSE_BUTTON_RIGHT,
	"middle": MOUSE_BUTTON_MIDDLE,
	"wheel_up": MOUSE_BUTTON_WHEEL_UP,
	"wheel_down": MOUSE_BUTTON_WHEEL_DOWN,
}


func _handle_input_post(peer: StreamPeerTCP, body: String) -> void:
	var parsed: Variant = JSON.parse_string(body)
	if not (parsed is Dictionary):
		_respond_json(peer, {"ok": false, "error": "body must be a JSON object"})
		return
	var payload: Dictionary = parsed
	var event_type: String = "%s" % payload.get("type", "")
	var event: InputEvent = null
	match event_type:
		"mouse_motion":
			var motion: InputEventMouseMotion = InputEventMouseMotion.new()
			motion.position = Vector2(float(payload.get("x", 0)), float(payload.get("y", 0)))
			motion.global_position = motion.position
			event = motion
		"mouse_button":
			var button_name: String = "%s" % payload.get("button", "left")
			if not _MOUSE_BUTTON_LOOKUP.has(button_name):
				_respond_json(peer, {"ok": false, "error": "unknown mouse button: %s" % button_name})
				return
			var btn: InputEventMouseButton = InputEventMouseButton.new()
			btn.button_index = int(_MOUSE_BUTTON_LOOKUP[button_name])
			btn.position = Vector2(float(payload.get("x", 0)), float(payload.get("y", 0)))
			btn.global_position = btn.position
			btn.pressed = bool(payload.get("pressed", true))
			event = btn
		"key":
			var key: InputEventKey = InputEventKey.new()
			var keycode: Variant = payload.get("keycode", 0)
			if keycode is String:
				key.keycode = OS.find_keycode_from_string(keycode)
			else:
				key.keycode = int(keycode)
			key.pressed = bool(payload.get("pressed", true))
			key.meta_pressed = bool(payload.get("meta", false))
			key.ctrl_pressed = bool(payload.get("ctrl", false))
			key.shift_pressed = bool(payload.get("shift", false))
			key.alt_pressed = bool(payload.get("alt", false))
			event = key
		_:
			_respond_json(peer, {"ok": false, "error": "unknown type: %s" % event_type})
			return
	var root: Window = get_tree().get_root()
	root.push_input(event)
	var focused: Control = root.gui_get_focus_owner()
	var result: Dictionary = {
		"ok": true,
		"event": str(event),
		"pushed_via": "root_window",
	}
	if focused != null:
		result["focused"] = str(focused.get_path())
	_respond_json(peer, result)


func _scene_dump(depth: int = _DEFAULT_TREE_DEPTH) -> Dictionary:
	var root: Node = _root_node()
	if root == null:
		return {"available": false}
	return {"available": true, "root": _node_to_dict(root, depth), "depth": depth}


func _node_to_dict(node: Node, depth: int) -> Dictionary:
	var data: Dictionary = {
		"name": node.name,
		"type": node.get_class(),
		"path": str(node.get_path()),
	}
	if node is Control:
		var ctrl: Control = node
		data["visible"] = ctrl.visible
		# is_visible_in_tree captures inherited visibility — what the user
		# actually sees. Test drivers should usually filter on this.
		data["visible_in_tree"] = ctrl.is_visible_in_tree()
		data["global_pos"] = {"x": ctrl.global_position.x, "y": ctrl.global_position.y}
		data["size"] = {"x": ctrl.size.x, "y": ctrl.size.y}
		if ctrl is BaseButton:
			var bb: BaseButton = ctrl
			data["disabled"] = bb.disabled
			if ctrl is Button:
				data["text"] = (ctrl as Button).text
		elif ctrl is Label:
			data["text"] = (ctrl as Label).text
	if depth > 0:
		var children: Array = []
		for child in node.get_children():
			children.append(_node_to_dict(child, depth - 1))
		data["children"] = children
	else:
		data["children_count"] = node.get_child_count()
	return data


func _visible_text() -> Dictionary:
	var items: Array = []
	var root: Node = _root_node()
	if root != null:
		_collect_text(root, items)
	return {"items": items, "count": items.size()}


# A Control is "visible to the user" only when every ancestor is visible too.
# Plain `visible` returns the local flag, which means hidden modals still leak
# their text into the inspector. is_visible_in_tree() walks parents and is the
# right gate for "what the user actually sees".
func _is_visible_to_user(node: Node) -> bool:
	if node is Control:
		return (node as Control).is_visible_in_tree()
	if node is CanvasItem:
		return (node as CanvasItem).is_visible_in_tree()
	return true


func _collect_text(node: Node, out: Array) -> void:
	if not _is_visible_to_user(node):
		return
	if node is RichTextLabel:
		var rt_text: String = (node as RichTextLabel).text
		if rt_text.strip_edges() != "":
			out.append({"path": str(node.get_path()), "type": "RichTextLabel", "text": rt_text})
	elif node is Label:
		var lb_text: String = (node as Label).text
		if lb_text.strip_edges() != "":
			out.append({"path": str(node.get_path()), "type": "Label", "text": lb_text})
	for child in node.get_children():
		_collect_text(child, out)


func _viewport_info() -> Dictionary:
	var info: Dictionary = {}
	var root: Window = get_tree().get_root() if get_tree() != null else null
	if root != null:
		info["root_size"] = {"x": root.size.x, "y": root.size.y}
		info["root_position"] = {"x": root.position.x, "y": root.position.y}
		info["content_scale_factor"] = root.content_scale_factor
	info["display_window_size"] = {
		"x": DisplayServer.window_get_size().x,
		"y": DisplayServer.window_get_size().y,
	}
	info["display_size"] = {
		"x": DisplayServer.screen_get_size().x,
		"y": DisplayServer.screen_get_size().y,
	}
	return info


func _screenshot_bytes() -> PackedByteArray:
	var viewport: Viewport = get_viewport()
	if viewport == null:
		return PackedByteArray()
	var texture: ViewportTexture = viewport.get_texture()
	if texture == null:
		return PackedByteArray()
	var image: Image = texture.get_image()
	if image == null or image.is_empty():
		return PackedByteArray()
	return image.save_png_to_buffer()


func _root_node() -> Node:
	if get_tree() != null:
		return get_tree().get_root()
	if _root_lookup.is_valid():
		var maybe: Variant = _root_lookup.call()
		if maybe is Node:
			return maybe
	return null


func _respond(peer: StreamPeerTCP, code: int, content_type: String, body: String) -> void:
	var bytes: PackedByteArray = body.to_utf8_buffer()
	_send_head_and_body(peer, code, content_type, bytes)


func _respond_json(peer: StreamPeerTCP, payload: Variant) -> void:
	_respond(peer, 200, "application/json", JSON.stringify(payload))


func _respond_png(peer: StreamPeerTCP, bytes: PackedByteArray) -> void:
	if bytes.is_empty():
		_respond(peer, 503, "text/plain", "screenshot unavailable")
		return
	_send_head_and_body(peer, 200, "image/png", bytes)


func _send_head_and_body(peer: StreamPeerTCP, code: int, content_type: String, body_bytes: PackedByteArray) -> void:
	var head: String = "HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %s\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n" % [
		code, content_type, body_bytes.size(),
	]
	peer.put_data(head.to_utf8_buffer())
	if body_bytes.size() > 0:
		peer.put_data(body_bytes)
	peer.disconnect_from_host()
