"""MCP server exposing godot-loop as tools an agent can call.

Run via:
    GODOT_LOOP_CONFIG=/path/to/godot-loop.toml \\
        python -m godot_loop.mcp_server

Or register as an MCP server in Claude Code, Codex, etc.

Requires the optional `mcp` extra:
    pip install 'godot-loop[mcp]'
"""

from __future__ import annotations

import base64
import os
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "godot-loop MCP server requires the `mcp` package.\n"
        "Install with:  pip install 'godot-loop[mcp]'"
    ) from exc

import requests

from . import runners
from .config import LoopConfig, load_config, resolve_inspect_port
from .utils import source_env_file

# Module-level state for the editor-less harness: launch_runtime stores the
# Popen + port here, and kill_runtime / wait_for_route consult it. None means
# nothing has been launched in this MCP-server process.
_LAUNCHED: dict[str, Any] | None = None


def _load_cfg() -> LoopConfig:
    explicit = os.environ.get("GODOT_LOOP_CONFIG")
    return load_config(
        start=Path.cwd(),
        explicit=Path(explicit) if explicit else None,
    )


def _inspector_base() -> str:
    # When launch_runtime forked a process in this MCP-server session, all
    # subsequent inspect calls target THAT process — even if godot-loop.toml
    # would otherwise resolve to a different port.
    if _LAUNCHED is not None:
        return _LAUNCHED["base_url"]
    cfg = _load_cfg()
    env = source_env_file(cfg.env_file) if cfg.env_file else {}
    port = resolve_inspect_port(cfg, env)
    if not port:
        raise RuntimeError(
            "no inspector port — set inspect_port in godot-loop.toml or "
            "BACKEND_PORT in the project .env, then relaunch the game with "
            "--inspect-port=N."
        )
    return f"http://127.0.0.1:{port}"


def _http_get_json(endpoint: str, timeout: float = 10.0) -> dict:
    base = _inspector_base()
    url = f"{base}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _http_get_bytes(endpoint: str, timeout: float = 10.0) -> bytes:
    base = _inspector_base()
    url = f"{base}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def _http_post_json(endpoint: str, payload: dict, timeout: float = 5.0) -> dict:
    base = _inspector_base()
    url = f"{base}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _walk_scene(node: dict, name: str) -> dict | None:
    if node.get("name") == name:
        return node
    for child in node.get("children", []):
        hit = _walk_scene(child, name)
        if hit:
            return hit
    return None


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("godot-loop")


def _do_inspect(endpoint: str) -> dict:
    """Implementation for the ``inspect`` MCP tool — extracted for unit testing.

    Tries JSON; on parse failure (e.g. /healthz returns plain "ok") falls
    back to ``{"text": ...}`` so the tool stays usable on text endpoints.
    """
    base = _inspector_base()
    url = f"{base}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    resp = requests.get(url, timeout=10.0)
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {"text": resp.text}


@mcp.tool()
def inspect(endpoint: str = "/scene") -> dict:
    """GET an endpoint from the running game's RuntimeInspectorServer.

    Common endpoints: /scene, /text, /viewport, /healthz, plus any
    project-registered providers (e.g. /state, /inventory, /cards).
    """
    return _do_inspect(endpoint)


@mcp.tool()
def scene(depth: int | None = None) -> dict:
    """Get the full scene tree (recursive, rooted at the Window).

    `depth` caps recursion (default 32 — covers any sane UI hierarchy; lower
    when the dump is too large). Each Control returns its name, type, path,
    visible, global_pos, and size — enough to find a node by name and click
    its center.
    """
    if depth is not None:
        return _http_get_json(f"/scene?depth={int(depth)}")
    return _http_get_json("/scene")


@mcp.tool()
def visible_text() -> dict:
    """Every visible Label and RichTextLabel text on screen, with node paths."""
    return _http_get_json("/text")


@mcp.tool()
def viewport_info() -> dict:
    """Window size, content scale, and display info."""
    return _http_get_json("/viewport")


@mcp.tool()
def screenshot(save_to: str | None = None) -> dict:
    """Fetch a PNG of the current viewport.

    Returns {"path": "..."} when save_to is provided, else
    {"base64_png": "..."} (use sparingly; PNGs are large).
    """
    png = _http_get_bytes("/screenshot.png", timeout=15.0)
    if save_to:
        out = Path(save_to)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(png)
        return {"path": str(out), "bytes": len(png)}
    return {"base64_png": base64.b64encode(png).decode("ascii"), "bytes": len(png)}


@mcp.tool()
def find_node(name: str, depth: int = 32) -> dict | None:
    """Walk the scene tree looking for a node by `name`.

    `depth` caps the underlying /scene recursion (default 32). Returns the
    node dict (with global_pos + size + text) plus computed center
    coordinates, or null if not found.
    """
    tree = _http_get_json(f"/scene?depth={int(depth)}")
    hit = _walk_scene(tree.get("root", {}), name)
    if not hit:
        return None
    out: dict[str, Any] = dict(hit)
    pos = hit.get("global_pos")
    size = hit.get("size")
    if isinstance(pos, dict) and isinstance(size, dict):
        out["center"] = {
            "x": pos["x"] + size["x"] / 2,
            "y": pos["y"] + size["y"] / 2,
        }
    return out


@mcp.tool()
def click(x: float, y: float, button: str = "left") -> dict:
    """Click at viewport coordinates `(x, y)`.

    Sends mouse_button down + up. button is one of left|right|middle|wheel_up|wheel_down.
    """
    down = _http_post_json("/input", {
        "type": "mouse_button", "button": button,
        "x": x, "y": y, "pressed": True,
    })
    up = _http_post_json("/input", {
        "type": "mouse_button", "button": button,
        "x": x, "y": y, "pressed": False,
    })
    return {"down": down, "up": up}


@mcp.tool()
def click_node(name: str, button: str = "left") -> dict:
    """Find a node by name and click its center.

    Combines find_node + click.  Returns the click result plus the
    node info that was clicked.
    """
    node = find_node(name)
    if not node or "center" not in node:
        return {"ok": False, "error": f"node {name!r} not found or has no Control geometry"}
    cx = node["center"]["x"]
    cy = node["center"]["y"]
    result = click(cx, cy, button=button)
    return {"ok": True, "node": node, "click": result}


@mcp.tool()
def mouse_move(x: float, y: float) -> dict:
    """Move the mouse to viewport coordinates `(x, y)`."""
    return _http_post_json("/input", {"type": "mouse_motion", "x": x, "y": y})


@mcp.tool()
def key_press(
    keycode: str,
    shift: bool = False,
    ctrl: bool = False,
    alt: bool = False,
    meta: bool = False,
) -> dict:
    """Press and release a key.

    keycode is a Godot key string (e.g. "A", "Enter", "Space", "Escape")
    or an integer keycode.
    """
    base: dict[str, Any] = {
        "type": "key", "keycode": keycode,
        "shift": shift, "ctrl": ctrl, "alt": alt, "meta": meta,
    }
    down = _http_post_json("/input", {**base, "pressed": True})
    up = _http_post_json("/input", {**base, "pressed": False})
    return {"down": down, "up": up}


@mcp.tool()
def run_e2e(headless: bool = False, api_base: str | None = None) -> dict:
    """Boot the game, assert log markers, capture a screenshot.

    Returns a dict with bootstrap_ok, markers_seen, screenshot_path,
    exit_code, and the log file path for diagnostics.
    """
    cfg = _load_cfg()
    result = runners.run_e2e(
        cfg,
        api_base=api_base,
        headless=headless,
        keep_output=True,
    )
    return {
        "exit_code": result.exit_code,
        "bootstrap_ok": result.bootstrap_ok,
        "markers_seen": result.markers_seen,
        "screenshot_path": str(result.screenshot_path) if result.screenshot_path else None,
        "output_log": str(result.output_log),
        "godot_log": str(result.godot_log) if result.godot_log else None,
    }


@mcp.tool()
def run_smoke(smoke_path: str, timeout_seconds: int = 60) -> dict:
    """Run a single *_smoke.gd file headlessly. Returns {exit_code: int}."""
    cfg = _load_cfg()
    rc = runners.run_smoke(cfg, Path(smoke_path), timeout_seconds=timeout_seconds)
    return {"exit_code": rc}


@mcp.tool()
def run_smokes(timeout_seconds: int = 60) -> dict:
    """Run every *_smoke.gd under the smokes dir. Returns {exit_code: int}."""
    cfg = _load_cfg()
    rc = runners.run_smokes(cfg, timeout_seconds=timeout_seconds)
    return {"exit_code": rc}


# ---------------------------------------------------------------------------
# BOU-891 — editor-less e2e harness tools.
# launch_runtime forks a Godot client pointed at a project; the others wrap
# the matching inspector routes (or its own subprocess handle for kill).
# ---------------------------------------------------------------------------


@mcp.tool()
def launch_runtime(
    repo_path: str,
    api_base: str | None = None,
    mode: str | None = None,
    inspect_port: int | None = None,
    access_token: str | None = None,
    extra_args: list[str] | None = None,
    pre_dash_args: list[str] | None = None,
    headless: bool = False,
    wait_seconds: float = 30.0,
    godot_binary: str = "godot",
) -> dict:
    """Fork a Godot client pointed at a project; wait until /healthz returns 200.

    Returns {pid, inspect_port, base_url, healthy, elapsed_seconds}. The Popen
    handle is stashed in module state so kill_runtime() can find it later.

    Argument layout matches Godot's CLI:
        godot [pre_dash_args] --headless? --path repo_path -- [project_user_args]

    pre_dash_args: forwarded to Godot itself (e.g. ["--script", "smoke.gd"]).
    extra_args:    forwarded to the project (consumed via OS.get_cmdline_user_args
                   alongside --inspect-port / --api-base / --mode).
    access_token:  short-hand for adding `--access-token=<value>` to the project
                   args. Convenience for the common case of launching against
                   a backend that requires bearer auth; the project decides how
                   it consumes the flag.
    """
    global _LAUNCHED
    project = os.path.abspath(repo_path)
    if inspect_port is None:
        try:
            cfg = _load_cfg()
            env = source_env_file(cfg.env_file) if cfg.env_file else {}
            inspect_port = resolve_inspect_port(cfg, env) or 9876
        except Exception:
            inspect_port = 9876

    cmd: list[str] = [godot_binary]
    if headless:
        cmd.append("--headless")
    if pre_dash_args:
        cmd.extend(pre_dash_args)
    cmd.extend(["--path", project, "--"])
    cmd.append(f"--inspect-port={inspect_port}")
    if api_base:
        cmd.append(f"--api-base={api_base}")
    if mode:
        cmd.append(f"--mode={mode}")
    if access_token:
        cmd.append(f"--access-token={access_token}")
    if extra_args:
        cmd.extend(extra_args)

    popen = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{inspect_port}"
    _LAUNCHED = {
        "pid": popen.pid,
        "inspect_port": inspect_port,
        "base_url": base_url,
        "popen": popen,
    }

    health = wait_for_route(path="/healthz", timeout_seconds=wait_seconds)
    return {
        "pid": popen.pid,
        "inspect_port": inspect_port,
        "base_url": base_url,
        "healthy": bool(health.get("ok")),
        "elapsed_seconds": health.get("elapsed_seconds"),
    }


@mcp.tool()
def kill_runtime(pid: int | None = None) -> dict:
    """Terminate the previously-launched runtime (SIGTERM, then SIGKILL after 5s)."""
    global _LAUNCHED
    if _LAUNCHED is None:
        return {"ok": False, "error": "no runtime launched in this MCP process"}
    if pid is not None and pid != _LAUNCHED["pid"]:
        return {
            "ok": False,
            "error": f"pid mismatch: stored {_LAUNCHED['pid']}, asked {pid}",
        }
    popen = _LAUNCHED["popen"]
    stored_pid = _LAUNCHED["pid"]
    timed_out = False
    try:
        popen.terminate()
        try:
            exit_code = popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            popen.kill()
            exit_code = popen.wait(timeout=2)
            timed_out = True
    finally:
        _LAUNCHED = None
    return {"ok": True, "pid": stored_pid, "exit_code": exit_code, "timed_out": timed_out}


@mcp.tool()
def wait_for_route(
    path: str,
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.2,
) -> dict:
    """Poll an inspector route until it returns HTTP 200 or timeout elapses.

    Falls back to _inspector_base() (godot-loop.toml-driven) when no runtime
    has been launched in-process — useful for attaching to a manually-launched
    game-client (e.g. from `gmake game-client-mcp`).
    """
    base = _inspector_base()
    url = f"{base}{path if path.startswith('/') else '/' + path}"
    start = time.monotonic()
    deadline = start + timeout_seconds
    attempts = 0
    last_status: int | None = None
    last_error: str | None = None
    while True:
        attempts += 1
        try:
            resp = requests.get(url, timeout=2.0)
            last_status = resp.status_code
            if resp.status_code == 200:
                return {
                    "ok": True,
                    "elapsed_seconds": time.monotonic() - start,
                    "attempts": attempts,
                    "last_status": resp.status_code,
                }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= deadline:
            break
        time.sleep(interval_seconds)
    out: dict[str, Any] = {
        "ok": False,
        "elapsed_seconds": time.monotonic() - start,
        "attempts": attempts,
    }
    if last_status is not None:
        out["last_status"] = last_status
    if last_error is not None:
        out["last_error"] = last_error
    return out


@mcp.tool()
def press_button(node_path: str) -> dict:
    """Find a Button at the given NodePath and emit its `pressed` signal.

    More reliable than /input for headless drivers: emits the signal directly
    so handlers fire whether or not the button has keyboard focus.
    """
    encoded = urllib.parse.quote(node_path, safe="")
    return _http_get_json(f"/press_button?path={encoded}")


@mcp.tool()
def signal_emit(
    node_path: str,
    signal_name: str,
    args: list | None = None,
) -> dict:
    """Emit an arbitrary signal on a node by NodePath.

    Use when `press_button` doesn't fit — e.g. the player-equivalent action
    is firing a custom signal on a non-BaseButton node (a card's `selected`,
    a menu item's `chosen`, a dialog row's `activated`).

    `args` is a list of JSON-native values passed positionally to the signal
    (must match the signal's declared signature on the receiver side). For
    signals with complex Godot types (Vector2/3, Resource refs), serialize
    on the caller side or rely on the receiver to coerce.

    Returns {ok, path, signal, args_count} on success, or {ok: false, error}
    when the node or signal doesn't exist.
    """
    return _http_post_json(
        "/emit_signal",
        {"path": node_path, "signal": signal_name, "args": args or []},
    )


@mcp.tool()
def node_properties(node_path: str, names: list[str] | None = None) -> dict:
    """Read live property values off a node by NodePath.

    Use for observing runtime state that isn't surfaced by `get_state()` —
    any autoload's `var` / `@export` field, a UI panel's current label text,
    or any script-defined property on an active node. Complements
    `get_scene_tree` (which only dumps Control visibility / position) with
    arbitrary property access.

    `names` is an optional list of property names. If omitted, returns every
    script-exported property on the node's attached script (excludes inherited
    engine properties to keep payloads small).

    Values that aren't JSON-native are coerced: Vector2/3/4 → {x,y[,z][,w]},
    Color → {r,g,b,a}, Rect2 → {x,y,w,h}, NodePath/StringName → string,
    Object refs → "<ClassName#instance_id>".

    Returns {ok, path, type, properties: {name: value, ...}, missing?: [...]}.
    """
    encoded_path = urllib.parse.quote(node_path, safe="")
    names_csv = ",".join(names) if names else ""
    encoded_names = urllib.parse.quote(names_csv, safe=",")
    return _http_get_json(f"/node_properties?path={encoded_path}&names={encoded_names}")


@mcp.tool()
def get_state() -> dict:
    """GET /state — the project-registered inspector provider.

    Projects register this via inspector.register_provider("/state", ...);
    the payload shape is project-specific (combat: combatants/round/log; etc).
    """
    return _http_get_json("/state")


@mcp.tool()
def get_scene_tree(depth: int | None = None) -> dict:
    """GET /scene_tree — current scene's node hierarchy (rooted at current_scene).

    `depth` caps recursion (default 32 — covers any sane UI hierarchy; lower
    when the dump is too large). Distinct from /scene which dumps the full
    SceneTree root (Window). Used to discover NodePaths for press_button
    without hardcoding them in tests.
    """
    if depth is not None:
        return _http_get_json(f"/scene_tree?depth={int(depth)}")
    return _http_get_json("/scene_tree")


def main() -> None:  # pragma: no cover
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
