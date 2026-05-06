# agentic-godot

End-to-end automation for Godot development — letting coding agents
build, run, and observe live Godot clients.

Currently ships **godot-loop**, a closed-loop validation harness that
turns the usual "did my change break the game?" loop into something a
machine can drive.  An addon stands up an HTTP runtime inspector inside
your Godot client; a Python CLI launches the engine with smoke-mode
flags, asserts log markers, captures screenshots, and pokes at the
running cockpit while it plays.

> **Status**: alpha (0.1).  Carved out of a production game-client
> harness.  The shape is stable; the public API may shift before 1.0.

```text
   ┌──────────────────────────────────────────────────────────────┐
   │  godot-loop run e2e                                           │
   │      │                                                        │
   │      ├── pre_launch hook  (project-specific self-heal)        │
   │      ├── health check     (curl your backend)                 │
   │      ├── godot launch     (--api-base / --user-dir-tag /      │
   │      │                     --auto-load-... / --inspect-port)  │
   │      │       │                                                │
   │      │       └── RuntimeInspectorServer @ 127.0.0.1:N         │
   │      │              GET /scene  /text  /viewport  /cards      │
   │      │              GET /screenshot.png                       │
   │      │              POST /input                               │
   │      ├── log-marker grep  (bootstrap_succeeded, ...)          │
   │      ├── screenshot capture                                    │
   │      └── exit 0/1                                             │
   └──────────────────────────────────────────────────────────────┘
```

## Why

`godot --headless` + GUT-style smokes prove your **components** work in
isolation.  They don't prove the **user-visible loop** works — bootstrap
finishing, the backend actually answering, the right scene being on
screen, no cards racing into the wrong order.  godot-loop fills that gap
with three repeatable tiers:

| Tier | What it proves | How |
|------|---------------|-----|
| **1 — Unit smokes** | Components in isolation | `godot-loop run smoke <name>` (your `*_smoke.gd` files, headless) |
| **2 — End-to-end** | User-visible bootstrap loop | `godot-loop run e2e` boots the client, asserts log markers, captures a windowed screenshot |
| **3 — Live trace** | Event-ordering / timing bugs | `--inspect-port=N` + `godot-loop inspect`/`trace`/`input` against the running cockpit |

Tiers 1 and 2 catch most regressions cheaply.  Tier 3 catches the bugs
that pass smokes-green because the smoke calls the store directly and
never sees the dispatcher's per-event ordering — the cockpit incident in
the production codebase that this was carved out of, and the reason
`/cards` + `/scene` exist as live endpoints.

## Install

```bash
# CLI
pip install -e /path/to/agentic-godot          # editable, while developing
# (or, when published)
# pip install godot-loop
```

```bash
# Addon — symlink (recommended) or copy into your Godot project
ln -s /path/to/agentic-godot/addon/godot_loop \
      your-godot-project/addons/godot_loop
```

## Quickstart

**1. Wire the addon into your project's bootstrap.**

```gdscript
extends Node

var launch_config: LoopLaunchConfig

func _ready() -> void:
    launch_config = LoopLaunchConfig.new()
    launch_config.apply_command_line_args(OS.get_cmdline_user_args())

    if launch_config.inspect_port > 0:
        var inspector := RuntimeInspectorServer.new()
        inspector.setup(launch_config.inspect_port)
        inspector.register_provider("/cards", func() -> Dictionary:
            return {"cards": my_card_store.cards})
        add_child(inspector)

    # ... your normal bootstrap.  When ready:
    print("bootstrap_succeeded")
    if launch_config.exit_after_bootstrap:
        await get_tree().create_timer(2.0).timeout
        get_tree().quit()
```

**2. Drop a `godot-loop.toml` at your repo root.**

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
pre_launch = "scripts/godot-loop-pre-launch.sh"  # optional
```

**3. Run it.**

```bash
godot-loop run e2e                                    # full bootstrap + screenshot
godot-loop run smoke launch_config_smoke.gd
godot-loop inspect --endpoint /cards
godot-loop trace --endpoint /cards --endpoint /text   # poll-on-change
godot-loop input mouse_button --button left --x 400 --y 300
```

## CLI reference

| Command | What it does |
|---------|--------------|
| `godot-loop run e2e` | Boot client headlessly with smoke flags, assert markers, capture PNG. Honours `--api-base`, `--headless`, `--screenshot-path`, `--keep-output`, `--extra ...`. |
| `godot-loop run smoke <gd_path>` | Run one `*_smoke.gd` file headless via `--script`.  Looks under `<project>/scripts/dev/` if the path is bare. |
| `godot-loop inspect` | `GET` an endpoint from a running `RuntimeInspectorServer`.  `--endpoint /scene` (default), `--save-to PATH` for `/screenshot.png`. |
| `godot-loop trace` | Poll inspector endpoints, print on change.  `--endpoint` is repeatable; `--interval` defaults to 1s. |
| `godot-loop input <type>` | `POST /input`.  Types: `mouse_button` (`--button`, `--x`, `--y`, `--pressed`/`--released`), `mouse_motion` (`--x`, `--y`), `key` (`--keycode`, modifiers). |

All commands accept `--config PATH` to override config-file discovery
(default: walk up from cwd looking for `godot-loop.toml`).

## Addon API

### `LoopLaunchConfig` (`RefCounted`)

Parses standard CLI flags from `OS.get_cmdline_user_args()`.  Fields:

| Flag | Field | Notes |
|------|-------|-------|
| `--api-base=URL` | `api_base_url` | IPv4-normalized (`localhost` → `127.0.0.1`) |
| `--access-token=TOKEN` | `bearer_token` | Optional |
| `--user-dir-tag=TAG` | `user_dir_tag` | Per-run scope for `user://` cache |
| `--auto-load-campaign=ID` | `auto_load_campaign` | Opaque token consumed by your bootstrap |
| `--exit-after-bootstrap` | `exit_after_bootstrap` (bool) | Quit once you signal ready |
| `--screenshot-after-ms=N` | `screenshot_after_ms` | When to capture |
| `--screenshot-path=PATH` | `screenshot_path` | Where to write |
| `--inspect-port=N` | `inspect_port` | Stand up RuntimeInspectorServer on `127.0.0.1:N` |

Subclass to add project-specific flags — call `super()` then walk the same args list.

### `RuntimeInspectorServer` (`Node`)

Localhost HTTP server.  Built-in endpoints:

| Method | Path | Returns |
|--------|------|---------|
| GET | `/healthz` | `ok` |
| GET | `/scene` | Recursive node tree dump (name, type, path, visible, global_pos/size, depth 8) |
| GET | `/text` | Every visible `Label` / `RichTextLabel` text + node path |
| GET | `/viewport` | Root window size/position, content scale, display info |
| GET | `/screenshot.png` | PNG of the current viewport |
| POST | `/input` | Inject InputEvent (`mouse_button` / `mouse_motion` / `key`) |

Register custom GET endpoints from your project:

```gdscript
inspector.register_provider("/cards", func() -> Dictionary:
    return {"cards": my_card_store.cards})
```

The provider is a `Callable` returning a `Dictionary`; it gets serialized
to JSON automatically.

## Configuration reference

`godot-loop.toml` lives at the consuming project's repo root.  Search
walks up from cwd, so any subdirectory works.

| Key | Default | What |
|-----|---------|------|
| `[project].path` | required | Path to the Godot project (dir containing `project.godot`), relative to the config file |
| `[project].env_file` | `null` | KEY=VALUE file the loop sources (e.g. for `BACKEND_PORT`) |
| `[health].url` | `null` | If set, `curl`'d before launch; non-2xx aborts |
| `[health].timeout_seconds` | `5.0` | Health-check timeout |
| `[e2e].launch_args` | `[]` | Extra args appended after `--`; `--api-base`/`--user-dir-tag` are added automatically |
| `[e2e].log_markers` | `[]` | Strings that must all appear in stdout |
| `[e2e].screenshot_after_ms` | `12000` | When to capture (windowed only) |
| `[e2e].timeout_seconds` | `60` | Hard wall-clock timeout |
| `[e2e].headless` | `false` | Force `--headless` (no screenshot possible) |
| `[user_dir_tag].strategy` | `worktree-basename` | Or `fixed`; how `--user-dir-tag` is built |
| `[user_dir_tag].prefix` | `loop` | Prepended to the basename |
| `[user_dir_tag].fixed` | `null` | Required when `strategy = "fixed"` |
| `[hooks].pre_launch` | `null` | Script run before each launch (project-specific self-heal) |
| `inspect_port` | `null` | Static inspector port (else `BACKEND_PORT + 100` from env_file) |

## Layout

```
agentic-godot/
├── addon/godot_loop/        # Godot 4 addon
│   ├── LoopLaunchConfig.gd
│   ├── RuntimeInspectorServer.gd
│   ├── plugin.cfg
│   └── plugin.gd
├── src/godot_loop/          # Python CLI
│   ├── cli.py
│   ├── config.py
│   ├── runners.py
│   └── utils.py
├── examples/godot-loop.toml
└── docs/INTEGRATING.md      # full integration walkthrough
```

## Project-specific concerns

godot-loop deliberately doesn't know about:

- Addon symlinks your project needs (e.g. third-party addons stored
  outside the repo)
- `class_name` cache rebuilds after edits
- Auth-token minting for dev backends
- Mode/profile/feature flags specific to your bootstrap

Put all of it in a `[hooks].pre_launch` script.  See `docs/INTEGRATING.md`
for the gaia consumer's example.

## Contributing

Issues and PRs welcome at
[github.com/Boundless-Studios/agentic-godot](https://github.com/Boundless-Studios/agentic-godot).
The CLI has zero tests today — first contribution is gladly accepted.

## License

MIT.  See [`LICENSE`](LICENSE).
