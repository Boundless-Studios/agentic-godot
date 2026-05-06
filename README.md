# agentic-godot

End to end automation for Godot development — letting coding agents
build, run, and observe live Godot clients.

Currently ships **godot-loop**: a closed-loop validation harness pairing a
small Godot addon (HTTP runtime inspector + standard CLI flag parser)
with a Python CLI.  The CLI drives the engine headlessly, asserts log
markers, captures screenshots, and lets you query the live cockpit while
the user (or another process) plays.

> **Status**: alpha.  Carved out of a production game-client harness; the
> shape is stable but the public API may shift before 1.0.

## Why godot-loop

`godot --headless` + GUT-style smokes prove your **components** work in
isolation.  They don't prove the **user-visible loop** works — bootstrap
finishing, the backend actually answering, the right scene being on screen.
godot-loop fills that gap with three repeatable tiers:

1. **Unit smokes** — your existing `*_smoke.gd` scripts, runnable via
   `godot-loop run smoke <name>`.
2. **End-to-end** — `godot-loop run e2e` boots the client with smoke flags
   (`--api-base`, `--user-dir-tag`, `--exit-after-bootstrap`,
   `--screenshot-after-ms`), tails stdout for log markers you declare,
   captures a windowed screenshot, exits non-zero if any assertion fails.
3. **Live runtime trace** — when launched with `--inspect-port=N`, the
   addon stands up a localhost HTTP server.  `godot-loop inspect`,
   `godot-loop trace`, and `godot-loop input` query it for cards, scene
   tree, visible text, screenshot, and inject input events.

## Install

```bash
pip install godot-loop          # CLI (when published)
# or, while developing:
pip install -e /path/to/agentic-godot
```

Drop the addon into your Godot project (symlink during development is the
ergonomic option):

```bash
ln -s /path/to/agentic-godot/addon/godot_loop \
      your-project/addons/godot_loop
```

## Quickstart

In your Godot project's main scene:

```gdscript
# parse standard flags
var launch_config := LoopLaunchConfig.new()
launch_config.apply_command_line_args(OS.get_cmdline_user_args())

# stand up the inspector if --inspect-port=N was passed
if launch_config.inspect_port > 0:
    var inspector := RuntimeInspectorServer.new()
    inspector.setup(launch_config.inspect_port)
    inspector.register_provider("/cards", func() -> Dictionary:
        return {"cards": my_card_store.cards})
    add_child(inspector)
```

Drop a `godot-loop.toml` at your repo root:

```toml
[project]
path = "clients/your-godot-project"
env_file = ".env"

[health]
url = "http://127.0.0.1:8000/api/health"

[e2e]
launch_args = ["--auto-load-campaign=first", "--exit-after-bootstrap"]
log_markers = ["bootstrap_succeeded", "auto_load_campaign_loaded:"]
screenshot_after_ms = 12000
timeout_seconds = 60

[hooks]
pre_launch = "scripts/godot-loop-pre-launch.sh"
```

Then:

```bash
godot-loop run e2e             # full bootstrap + screenshot
godot-loop run smoke launch_config_smoke.gd
godot-loop inspect --endpoint /cards
godot-loop trace
godot-loop input mouse_button --button left --x 400 --y 300
```

## Layout

```
agentic-godot/
├── addon/godot_loop/        # Godot 4 addon (drop into your addons/)
│   ├── LoopLaunchConfig.gd
│   ├── RuntimeInspectorServer.gd
│   └── plugin.cfg
├── src/godot_loop/          # Python CLI package
│   ├── cli.py
│   ├── config.py
│   ├── runners.py
│   └── utils.py
├── examples/godot-loop.toml
└── docs/INTEGRATING.md
```

## License

MIT.
