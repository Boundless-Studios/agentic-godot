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


def _load_cfg() -> LoopConfig:
    explicit = os.environ.get("GODOT_LOOP_CONFIG")
    return load_config(
        start=Path.cwd(),
        explicit=Path(explicit) if explicit else None,
    )


def _inspector_base() -> str:
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

    JSON responses are returned as parsed dicts; non-JSON responses
    (e.g. /healthz returns plain "ok") are wrapped as ``{"text": ...}``
    so the tool never raises on text-typed endpoints.
    """
    base = _inspector_base()
    url = f"{base}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    resp = requests.get(url, timeout=10.0)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type == "application/json":
        return resp.json()
    return {"text": resp.text, "content_type": content_type or "unknown"}


@mcp.tool()
def inspect(endpoint: str = "/scene") -> dict:
    """GET an endpoint from the running game's RuntimeInspectorServer.

    Common endpoints: /scene, /text, /viewport, /healthz, plus any
    project-registered providers (e.g. /state, /inventory, /cards).
    """
    return _do_inspect(endpoint)


@mcp.tool()
def scene() -> dict:
    """Get the full scene tree (recursive, depth 8).

    Each Control returns its name, type, path, visible, global_pos, and
    size — enough to find a node by name and click its center.
    """
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
def find_node(name: str) -> dict | None:
    """Walk the scene tree looking for a node by `name`.

    Returns the node dict (with global_pos + size + text) plus computed
    center coordinates, or null if not found.
    """
    tree = _http_get_json("/scene")
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


def main() -> None:  # pragma: no cover
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
