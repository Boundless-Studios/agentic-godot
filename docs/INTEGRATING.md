# Integrating godot-loop into an existing Godot project

This guide walks through what a consumer project needs to do.

## 1. Make the addon discoverable

Either symlink (preferred while you iterate on godot-loop itself) or copy
`addon/godot_loop` into the project's `addons/godot_loop`.

The classes the addon exposes:

- `LoopLaunchConfig` — `RefCounted`, parses standard CLI flags
- `RuntimeInspectorServer` — `Node`, localhost HTTP debug server

Both use `class_name`, so once the addon is on disk you can reference them
from any GDScript without an explicit `preload`.

## 2. Wire LoopLaunchConfig into your bootstrap

In your main scene's bootstrap:

```gdscript
var launch_config := LoopLaunchConfig.new()
launch_config.apply_command_line_args(OS.get_cmdline_user_args())

# api_base_url, bearer_token, user_dir_tag, auto_load_campaign,
# exit_after_bootstrap, screenshot_after_ms, screenshot_path,
# inspect_port are now populated.

if launch_config.user_dir_tag != "":
    # Scope your user:// cache directory by the tag so concurrent runs
    # in different worktrees don't collide on cached api_base_url etc.
    cache = MyLocalCache.new(launch_config.user_dir_tag)
```

If your project also has its own flags, subclass `LoopLaunchConfig` and
walk the same args list inside `apply_command_line_args`.

## 3. Stand up the inspector

```gdscript
if launch_config.inspect_port > 0:
    var inspector := RuntimeInspectorServer.new()
    inspector.setup(launch_config.inspect_port)
    inspector.register_provider("/cards", func() -> Dictionary:
        return _cards_payload())
    add_child(inspector)
```

Built-in routes are always on: `/healthz`, `/scene`, `/text`, `/viewport`,
`/screenshot.png`, and `POST /input`.

## 4. Honour --exit-after-bootstrap and --screenshot-after-ms

The addon does not enforce these — your bootstrap signals "ready" however
it does today.  Add two short hooks:

```gdscript
func _on_bootstrap_succeeded() -> void:
    print("bootstrap_succeeded")
    if launch_config.screenshot_after_ms > 0 and launch_config.screenshot_path != "":
        get_tree().create_timer(launch_config.screenshot_after_ms / 1000.0).timeout.connect(
            _save_screenshot.bind(launch_config.screenshot_path))
    if launch_config.exit_after_bootstrap:
        # Give the screenshot timer a beat to fire before quitting.
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

This is where project-specific weirdness lives — class cache rebuilds,
addon symlinks, dev-token mints.  godot-loop itself stays project-agnostic.

```bash
#!/usr/bin/env bash
set -euo pipefail
# scripts/godot-loop-pre-launch.sh — gaia example
ROOT="$(git rev-parse --show-toplevel)"

# Self-heal addon symlinks if missing
[[ -e "$ROOT/clients/x/addons/godot_ai" ]] || ln -s ~/.local/share/godot-ai/plugin/addons/godot_ai "$ROOT/clients/x/addons/godot_ai"

# Mint a dev token and stash it for Main.gd to find
bash "$ROOT/scripts/dev/mint-dev-token.sh"
```

## 7. Run it

```bash
godot-loop run e2e
godot-loop run smoke launch_config_smoke.gd
godot-loop inspect --endpoint /cards
godot-loop trace
```
