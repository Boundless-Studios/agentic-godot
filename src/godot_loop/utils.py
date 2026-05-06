"""Small utilities shared by the CLI commands."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable


def find_godot_binary() -> str:
    """Locate a usable Godot binary.

    Honours $GODOT first, then falls back to PATH lookup of `godot`.
    Raises FileNotFoundError if nothing usable is found.
    """
    explicit = os.environ.get("GODOT")
    if explicit:
        if Path(explicit).is_file() and os.access(explicit, os.X_OK):
            return explicit
        raise FileNotFoundError(f"$GODOT={explicit!r} is not an executable file")
    found = shutil.which("godot")
    if found:
        return found
    raise FileNotFoundError(
        "godot binary not found. Install Godot 4.x or set $GODOT to its path."
    )


def normalize_api_base(url: str) -> str:
    """Mirror of LoopLaunchConfig.normalize_api_base — force IPv4."""
    if not url:
        return url
    out = url.strip()
    out = out.replace("://localhost:", "://127.0.0.1:")
    out = out.replace("://localhost/", "://127.0.0.1/")
    if out.endswith("://localhost"):
        out = out[: -len("://localhost")] + "://127.0.0.1"
    return out


def worktree_basename(start: Path) -> str:
    """Sanitized basename of `start` — used for per-worktree user-dir tags."""
    name = start.resolve().name
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def source_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file into a dict.

    Strips surrounding quotes and skips comments / blank lines.  Does NOT
    expand variables — most worktree .env files are static.
    """
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def stream_subprocess(
    cmd: list[str],
    *,
    timeout: float | None,
    stdout_path: Path,
    extra_env: dict[str, str] | None = None,
) -> int:
    """Run `cmd`, redirecting stdout+stderr to `stdout_path`.  Returns exit code.

    Returns 124 on timeout (matching coreutils convention).
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    with stdout_path.open("wb") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, env=env)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return 124


def grep_any(path: Path, needles: Iterable[str]) -> dict[str, bool]:
    """Return a dict of needle -> bool indicating whether it appeared in `path`."""
    result = {needle: False for needle in needles}
    if not path.is_file():
        return result
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            for needle in result:
                if not result[needle] and needle in line:
                    result[needle] = True
            if all(result.values()):
                break
    return result
