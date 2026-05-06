# Integrating godot-loop into an existing Godot project

This guide walks through what a project needs to do to use the harness.

## 1. Make the addon discoverable

Either symlink (preferred while you iterate on godot-loop itself) or copy
`addon/godot_loop` into the project's `addons/godot_loop`.

The classes the addon exposes:

- `LoopLaunchConfig` — `RefCounted`, parses standard CLI flags.
- `RuntimeInspectorServer` — `Node`, localhost HTTP debug server.

Both use `class_name`, so once the addon is on disk you can reference
them from any GDScript without an explicit `preload`.

## 2. Wire LoopLaunchConfig into your bootstrap

In your main scene's bootstrap:

```gdscript
var launch_config := LoopLaunchConfig.new()
launch_config.apply_command_line_args(OS.get_cmdline_user_args())

# api_base_url, bearer_token, user_dir_tag, exit_after_bootstrap,
# screenshot_after_ms, screenshot_path, inspect_port are now populated.

if launch_config.user_dir_tag != "":
    # Scope your user:// cache by the tag so concurrent runs in different
    # worktrees don't collide on cached state.
    cache = MyLocalCache.new(launch_config.user_dir_tag)
```

If your project has its own flags, subclass `LoopLaunchConfig` and walk
the same args list inside `apply_command_line_args`.

## 3. Stand up the inspector

```gdscript
if launch_config.inspect_port > 0:
    var inspector := RuntimeInspectorServer.new()
    inspector.setup(launch_config.inspect_port)
    inspector.register_provider("/state", func() -> Dictionary:
        return _state_payload())
    add_child(inspector)
```

Built-in routes are always on: `/healthz`, `/scene`, `/text`, `/viewport`,
`/screenshot.png`, and `POST /input`.

## 4. Honour --exit-after-bootstrap and --screenshot-after-ms

The addon does not enforce these — your bootstrap signals "ready" however
it does today.  Add two short hooks:

```gdscript
func _on_ready() -> void:
    print("ready")        # matches log_markers in your godot-loop.toml
    if launch_config.screenshot_after_ms > 0 and launch_config.screenshot_path != "":
        get_tree().create_timer(launch_config.screenshot_after_ms / 1000.0).timeout.connect(
            _save_screenshot.bind(launch_config.screenshot_path))
    if launch_config.exit_after_bootstrap:
        # Let the screenshot timer fire first, then quit.
        get_tree().create_timer(2.0 + launch_config.screenshot_after_ms / 1000.0).timeout.connect(
            get_tree().quit)
```

## 5. Drop in a `godot-loop.toml`

Copy `examples/godot-loop.toml` to the root of your repo.  Fill in:

- `[project].path` — relative path to the Godot project
- `[health].url` — backend health endpoint (or remove the section)
- `[e2e].launch_args` — your project-specific bootstrap flags
- `[e2e].log_markers` — strings the run is expected to print to stdout
- `[hooks].pre_launch` — a script that does any per-run self-heal

## 6. (Optional) pre_launch hook

This is where project-specific concerns live — class cache rebuilds,
addon symlinks, dev tokens, anything else your project needs to do once
per run.  godot-loop itself stays project-agnostic.

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"

# Example: self-heal a third-party addon symlink
[[ -e "$ROOT/path/to/addons/some_addon" ]] \
  || ln -s ~/.local/share/some_addon "$ROOT/path/to/addons/some_addon"

# Example: rebuild Godot's class_name cache after a code change
godot --headless --editor --quit-after 200 --path "$ROOT/path/to/project" >/dev/null 2>&1 || true
```

## 7. Run it

```bash
godot-loop run e2e
godot-loop run smoke my_smoke.gd
godot-loop inspect --endpoint /state
godot-loop trace --endpoint /state
```
