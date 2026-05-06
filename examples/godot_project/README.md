# godot-loop example project

Smallest possible Godot 4 project that uses the `godot_loop` addon.

## What's here

- `project.godot` + `Main.tscn` + `Main.gd` — a single button that
  increments a counter and updates a label when clicked.
- `godot-loop.toml` — points at this directory; configured for
  `godot-loop run e2e`.

## Try it

From this directory:

```bash
# 1. Symlink the addon into addons/
ln -s ../../addon/godot_loop addons/godot_loop

# 2. Install the CLI (or use python -m godot_loop from the repo)
pip install -e ../..

# 3. End-to-end: bootstrap, capture a screenshot, exit.
godot-loop run e2e
ls .godot-loop-screenshot.png

# 4. Or launch with the inspector + drive it from another terminal:
godot --path . -- --inspect-port=8765
# (in another terminal)
godot-loop inspect --endpoint /scene
godot-loop inspect --endpoint /state
godot-loop input mouse_button --x 480 --y 290    # clicks the button
godot-loop inspect --endpoint /state              # clicks should be 1 now
```
