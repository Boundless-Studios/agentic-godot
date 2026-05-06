# godot_loop addon

Drop-in Godot 4 addon that ships two runtime helpers:

- `LoopLaunchConfig.gd` — parses standard CLI flags
  (`--api-base=`, `--user-dir-tag=`, `--auto-load-campaign=`,
  `--exit-after-bootstrap`, `--screenshot-after-ms=`, `--screenshot-path=`,
  `--inspect-port=`, `--access-token=`).  Use directly or extend with
  project-specific flags.

- `RuntimeInspectorServer.gd` — localhost-only HTTP server that exposes
  `/healthz`, `/scene`, `/text`, `/viewport`, `/screenshot.png`, and
  `/input` (POST) on a port chosen by `--inspect-port`.  Project code
  registers extra GET endpoints via `register_provider("/cards", ...)`.

## Install

Either:

1. **Symlink** (recommended during development):
   ```bash
   ln -s /path/to/godot-loop/addon/godot_loop \
         your-project/addons/godot_loop
   ```

2. **Copy** the `addon/godot_loop` directory into `addons/godot_loop` of
   your Godot project.

The addon does not ship an autoload — wire it into your bootstrap node
yourself (see `docs/INTEGRATING.md` in the parent repo).

## Pair with the Python CLI

The `godot-loop` CLI consumes the same flags the addon parses.  See
the parent repo README for `godot-loop run e2e`, `godot-loop inspect`,
`godot-loop trace`.
