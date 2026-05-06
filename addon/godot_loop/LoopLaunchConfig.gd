extends RefCounted
class_name LoopLaunchConfig

# Standard CLI flags consumed by the godot-loop harness.  A project can use
# this RefCounted directly, or extend it and add project-specific flags.
#
#   --api-base=URL              backend base URL (sanitized to IPv4)
#   --user-dir-tag=TAG          per-run user:// scope (avoids cross-run cache poison)
#   --auto-load-campaign=ID     opaque token consumed by the project bootstrap
#   --exit-after-bootstrap      quit once bootstrap_succeeded fires
#   --screenshot-after-ms=N     N ms after _ready, save the viewport
#   --screenshot-path=PATH      where to save it
#   --inspect-port=N            stand up RuntimeInspectorServer on 127.0.0.1:N
#   --access-token=TOKEN        bearer token to inject (optional)

var api_base_url: String = ""
var bearer_token: String = ""
var user_dir_tag: String = ""
var auto_load_campaign: String = ""
var exit_after_bootstrap: bool = false
var screenshot_after_ms: int = 0
var screenshot_path: String = ""
var inspect_port: int = 0


# Returns true if any flag was applied.  Subclasses can call super() then
# walk the same args list for their own prefixes.
func apply_command_line_args(args: PackedStringArray) -> bool:
	var changed: bool = false
	for arg in args:
		if arg.begins_with("--api-base="):
			api_base_url = normalize_api_base(arg.substr("--api-base=".length()))
			changed = true
		elif arg.begins_with("--access-token="):
			bearer_token = arg.substr("--access-token=".length())
			changed = true
		elif arg.begins_with("--user-dir-tag="):
			user_dir_tag = arg.substr("--user-dir-tag=".length()).strip_edges()
			changed = true
		elif arg.begins_with("--auto-load-campaign="):
			auto_load_campaign = arg.substr("--auto-load-campaign=".length()).strip_edges()
			changed = true
		elif arg == "--exit-after-bootstrap":
			exit_after_bootstrap = true
			changed = true
		elif arg.begins_with("--screenshot-after-ms="):
			screenshot_after_ms = int(arg.substr("--screenshot-after-ms=".length()))
			changed = true
		elif arg.begins_with("--screenshot-path="):
			screenshot_path = arg.substr("--screenshot-path=".length()).strip_edges()
			changed = true
		elif arg.begins_with("--inspect-port="):
			inspect_port = int(arg.substr("--inspect-port=".length()))
			changed = true
	return changed


# Force `localhost` -> `127.0.0.1`.  Godot's HTTPRequest on macOS resolves
# `localhost` to ::1 first; if the backend only binds the IPv4 mapping, the
# request silently returns status 0 with no body.  Pinning to 127.0.0.1 makes
# the failure mode (or success) visible.
static func normalize_api_base(url: String) -> String:
	var trimmed: String = url.strip_edges()
	if trimmed == "":
		return trimmed
	trimmed = trimmed.replace("://localhost:", "://127.0.0.1:")
	trimmed = trimmed.replace("://localhost/", "://127.0.0.1/")
	if trimmed.ends_with("://localhost"):
		trimmed = trimmed.substr(0, trimmed.length() - "://localhost".length()) + "://127.0.0.1"
	return trimmed
