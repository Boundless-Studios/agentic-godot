extends Node
class_name RuntimeInspectorServer

# Localhost HTTP inspector for a running Godot client.  Opt-in via
# --inspect-port=N (or call setup() directly with a port) — when set, opens
# 127.0.0.1:N and serves these read-only built-in endpoints:
#
#   GET  /healthz         -> "ok"
#   GET  /scene           -> {root: {name, type, path, visible, children: [...]}}
#   GET  /text            -> {items: [{path, type, text}, ...]}
#   GET  /viewport        -> root_size, root_position, content_scale, display info
#   GET  /screenshot.png  -> PNG of the current viewport
#   POST /input           -> inject an InputEvent (mouse_button|mouse_motion|key)
#
# Projects can register additional GET endpoints via register_provider():
#
#   inspector.register_provider("/cards", func() -> Dictionary:
#       return {"cards": my_store.cards})
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
			_:
				_respond(peer, 404, "text/plain", "not found")
	else:
		_respond(peer, 405, "text/plain", "method not allowed")
	return false


func _handle_get(peer: StreamPeerTCP, path: String) -> void:
	if _providers.has(path):
		var provider: Callable = _providers[path]
		if provider.is_valid():
			var payload: Variant = provider.call()
			_respond_json(peer, payload)
			return
	match path:
		"/healthz":
			_respond(peer, 200, "text/plain", "ok\n")
		"/scene":
			_respond_json(peer, _scene_dump())
		"/text":
			_respond_json(peer, _visible_text())
		"/screenshot.png":
			_respond_png(peer, _screenshot_bytes())
		"/viewport":
			_respond_json(peer, _viewport_info())
		_:
			_respond(peer, 404, "text/plain", "not found")


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


func _scene_dump() -> Dictionary:
	var root: Node = _root_node()
	if root == null:
		return {"available": false}
	return {"available": true, "root": _node_to_dict(root, 8)}


func _node_to_dict(node: Node, depth: int) -> Dictionary:
	var data: Dictionary = {
		"name": node.name,
		"type": node.get_class(),
		"path": str(node.get_path()),
	}
	if node is Control:
		var ctrl: Control = node
		data["visible"] = ctrl.visible
		data["global_pos"] = {"x": ctrl.global_position.x, "y": ctrl.global_position.y}
		data["size"] = {"x": ctrl.size.x, "y": ctrl.size.y}
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


func _collect_text(node: Node, out: Array) -> void:
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
