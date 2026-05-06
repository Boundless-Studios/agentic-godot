"""Implementations of the godot-loop CLI subcommands."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests

from .config import LoopConfig, resolve_inspect_port, resolve_user_dir_tag
from .utils import (
    find_godot_binary,
    grep_any,
    normalize_api_base,
    source_env_file,
    stream_subprocess,
)


def _print(msg: str) -> None:
    print(msg, flush=True)


def _run_pre_launch_hook(cfg: LoopConfig) -> None:
    hook = cfg.hooks.pre_launch
    if not hook:
        return
    hook_path = (cfg.config_root / hook).resolve()
    if not hook_path.is_file():
        _print(f"WARN: pre_launch hook not found: {hook_path}")
        return
    _print(f"==> pre_launch: {hook_path}")
    rc = subprocess.call([str(hook_path)], cwd=cfg.config_root)
    if rc != 0:
        raise SystemExit(f"pre_launch hook exited {rc}")


def _check_health(cfg: LoopConfig) -> None:
    if not cfg.health.url:
        return
    url = normalize_api_base(cfg.health.url)
    _print(f"==> Health check: {url}")
    try:
        resp = requests.get(url, timeout=cfg.health.timeout_seconds)
        if resp.status_code >= 400:
            raise SystemExit(
                f"FAIL: backend health check at {url} returned {resp.status_code}"
            )
    except requests.RequestException as exc:
        raise SystemExit(f"FAIL: backend health check at {url}: {exc}")
    _print("    ok")


@dataclass
class E2EResult:
    exit_code: int
    bootstrap_ok: bool
    markers_seen: dict[str, bool]
    screenshot_path: Path | None
    output_log: Path
    godot_log: Path | None


def run_e2e(
    cfg: LoopConfig,
    *,
    api_base: str | None = None,
    headless: bool | None = None,
    screenshot_path: Path | None = None,
    keep_output: bool = False,
    extra_args: Iterable[str] | None = None,
) -> E2EResult:
    """Boot the project headlessly with smoke flags, assert log markers,
    capture a screenshot."""

    env_vars = source_env_file(cfg.env_file) if cfg.env_file else {}
    backend_port = env_vars.get("BACKEND_PORT", "8000")

    api_base_url = api_base or os.environ.get("API_BASE")
    if not api_base_url:
        # Fall back: derive from config.health.url's host:port if present, or
        # use BACKEND_PORT from the env file.
        if cfg.health.url:
            api_base_url = cfg.health.url.rsplit("/api/", 1)[0]
        else:
            api_base_url = f"http://127.0.0.1:{backend_port}"
    api_base_url = normalize_api_base(api_base_url)

    headless_mode = cfg.e2e.headless if headless is None else headless
    user_dir_tag = resolve_user_dir_tag(cfg)
    inspect_port = resolve_inspect_port(cfg, env_vars)

    work_dir = Path(tempfile.mkdtemp(prefix="godot-loop-e2e."))
    output_log = work_dir / "output.log"
    godot_log = work_dir / "godot.log"
    captured_screenshot = work_dir / "screenshot.png"

    _print("==> godot-loop run e2e")
    _print(f"    project:    {cfg.project_path}")
    _print(f"    api_base:   {api_base_url}")
    _print(f"    user_dir:   {user_dir_tag}")
    _print(f"    headless:   {headless_mode}")
    _print(f"    inspect:    {inspect_port if inspect_port else 'off'}")

    _check_health(cfg)
    _run_pre_launch_hook(cfg)

    godot = find_godot_binary()

    base_args = [
        f"--api-base={api_base_url}",
        f"--user-dir-tag={user_dir_tag}",
    ]
    base_args.extend(cfg.e2e.launch_args)
    if inspect_port:
        base_args.append(f"--inspect-port={inspect_port}")
    if not headless_mode:
        base_args.append(f"--screenshot-after-ms={cfg.e2e.screenshot_after_ms}")
        base_args.append(f"--screenshot-path={captured_screenshot}")
    if extra_args:
        base_args.extend(extra_args)

    cmd: list[str] = [godot, "--path", str(cfg.project_path), "--log-file", str(godot_log)]
    if headless_mode:
        cmd.append("--headless")
    cmd.append("--")
    cmd.extend(base_args)

    _print("==> launching: " + " ".join(cmd))
    rc = stream_subprocess(cmd, timeout=cfg.e2e.timeout_seconds, stdout_path=output_log)
    _print(f"==> godot exit={rc}")

    markers = list(cfg.e2e.log_markers) or ["bootstrap_succeeded"]
    seen = grep_any(output_log, markers)
    bootstrap_ok = all(seen.values())
    final_screenshot: Path | None = None
    if not headless_mode and captured_screenshot.is_file() and captured_screenshot.stat().st_size > 0:
        final_screenshot = screenshot_path or (cfg.config_root / ".godot-loop-screenshot.png")
        shutil.copyfile(captured_screenshot, final_screenshot)
        _print(f"==> screenshot: {final_screenshot}")

    _print("==> markers")
    for needle, present in seen.items():
        _print(f"    {needle:<32} = {present}")

    if not keep_output:
        shutil.rmtree(work_dir, ignore_errors=True)
    else:
        _print(f"    output kept at {work_dir}")

    return E2EResult(
        exit_code=rc,
        bootstrap_ok=bootstrap_ok,
        markers_seen=seen,
        screenshot_path=final_screenshot,
        output_log=output_log,
        godot_log=godot_log,
    )


# ---------------------------------------------------------------------------
# `godot-loop run smoke <name>` — run a single *_smoke.gd file headlessly.
# ---------------------------------------------------------------------------

def run_smoke(cfg: LoopConfig, smoke_path: Path, *, timeout_seconds: int = 60) -> int:
    if not smoke_path.is_file():
        # Allow lookup relative to project_path/scripts/dev.
        candidate = cfg.project_path / "scripts" / "dev" / smoke_path.name
        if candidate.is_file():
            smoke_path = candidate
        else:
            raise SystemExit(f"smoke file not found: {smoke_path}")
    godot = find_godot_binary()
    _print(f"==> godot-loop run smoke {smoke_path}")
    cmd = [
        godot,
        "--headless",
        "--path",
        str(cfg.project_path),
        "--quit",
        "--script",
        str(smoke_path.relative_to(cfg.project_path)) if smoke_path.is_relative_to(cfg.project_path) else str(smoke_path),
    ]
    return subprocess.call(cmd, cwd=cfg.config_root, timeout=timeout_seconds)


# ---------------------------------------------------------------------------
# `godot-loop inspect` — query a running RuntimeInspectorServer.
# ---------------------------------------------------------------------------

def _inspector_base(cfg: LoopConfig, port: int | None) -> str:
    env_vars = source_env_file(cfg.env_file) if cfg.env_file else {}
    resolved = resolve_inspect_port(cfg, env_vars, explicit=port)
    if not resolved:
        raise SystemExit(
            "no inspect port — pass --port, set inspect_port in config, "
            "or set BACKEND_PORT in the project .env"
        )
    return f"http://127.0.0.1:{resolved}"


def inspect(
    cfg: LoopConfig,
    *,
    port: int | None,
    endpoint: str,
    save_to: Path | None = None,
) -> int:
    base = _inspector_base(cfg, port)
    url = f"{base}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    _print(f"==> GET {url}")
    try:
        resp = requests.get(url, timeout=10)
    except requests.RequestException as exc:
        _print(f"FAIL: {exc}")
        return 3
    if resp.status_code != 200:
        _print(f"FAIL: status={resp.status_code} body={resp.text[:200]}")
        return resp.status_code
    if endpoint.endswith(".png") or "image/png" in resp.headers.get("Content-Type", ""):
        target = save_to or Path("/tmp/godot-loop-screenshot.png")
        target.write_bytes(resp.content)
        _print(f"    saved {len(resp.content)} bytes -> {target}")
        return 0
    if "application/json" in resp.headers.get("Content-Type", ""):
        print(json.dumps(resp.json(), indent=2))
    else:
        print(resp.text)
    return 0


# ---------------------------------------------------------------------------
# `godot-loop trace` — poll inspector while the user drives the client.
# ---------------------------------------------------------------------------

def trace(
    cfg: LoopConfig,
    *,
    port: int | None,
    interval: float = 1.0,
    endpoints: Iterable[str] = ("/scene", "/text"),
) -> int:
    base = _inspector_base(cfg, port)
    _print(f"==> tracing {base} (Ctrl-C to stop)")
    last: dict[str, str] = {}
    try:
        while True:
            for ep in endpoints:
                try:
                    resp = requests.get(f"{base}{ep}", timeout=5)
                    body = resp.text if resp.status_code == 200 else f"<{resp.status_code}>"
                except requests.RequestException as exc:
                    body = f"<error: {exc}>"
                if body != last.get(ep):
                    print(f"\n--- {ep} @ {time.strftime('%H:%M:%S')} ---")
                    try:
                        print(json.dumps(json.loads(body), indent=2))
                    except (ValueError, TypeError):
                        print(body)
                    last[ep] = body
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# `godot-loop input` — POST an InputEvent to the inspector.
# ---------------------------------------------------------------------------

def send_input(
    cfg: LoopConfig,
    *,
    port: int | None,
    payload: dict,
) -> int:
    base = _inspector_base(cfg, port)
    url = f"{base}/input"
    _print(f"==> POST {url} {json.dumps(payload)}")
    try:
        resp = requests.post(url, json=payload, timeout=5)
    except requests.RequestException as exc:
        _print(f"FAIL: {exc}")
        return 3
    if resp.status_code != 200:
        _print(f"FAIL: status={resp.status_code} body={resp.text[:200]}")
        return resp.status_code
    print(json.dumps(resp.json(), indent=2))
    return 0
