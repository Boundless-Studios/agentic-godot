"""TOML config loader for godot-loop."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


DEFAULT_FILENAMES = ("godot-loop.toml", ".godot-loop.toml")


@dataclass
class E2EConfig:
    launch_args: list[str] = field(default_factory=list)
    log_markers: list[str] = field(default_factory=list)
    screenshot_after_ms: int = 12000
    timeout_seconds: int = 60
    headless: bool = False


@dataclass
class HealthConfig:
    url: str | None = None
    timeout_seconds: float = 5.0


@dataclass
class UserDirTagConfig:
    strategy: str = "worktree-basename"   # or "fixed"
    prefix: str = "loop"
    fixed: str | None = None


@dataclass
class HooksConfig:
    pre_launch: str | None = None


@dataclass
class SmokesConfig:
    path: str = "scripts/dev"
    pattern: str = "*_smoke.gd"


@dataclass
class LoopConfig:
    project_path: Path
    env_file: Path | None = None
    health: HealthConfig = field(default_factory=HealthConfig)
    e2e: E2EConfig = field(default_factory=E2EConfig)
    user_dir_tag: UserDirTagConfig = field(default_factory=UserDirTagConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    smokes: SmokesConfig = field(default_factory=SmokesConfig)
    inspect_port: int | None = None
    config_path: Path | None = None
    config_root: Path = field(default_factory=lambda: Path.cwd())


def find_config(start: Path) -> Path | None:
    cur = start.resolve()
    while True:
        for name in DEFAULT_FILENAMES:
            candidate = cur / name
            if candidate.is_file():
                return candidate
        if cur.parent == cur:
            return None
        cur = cur.parent


def load_config(start: Path | None = None, *, explicit: Path | None = None) -> LoopConfig:
    """Load a LoopConfig from disk.

    If `explicit` is set, load that file.  Otherwise walk up from `start`
    (default cwd) looking for godot-loop.toml.  If no file is found, raise
    FileNotFoundError — the CLI surfaces this as a friendly error.
    """
    path = explicit
    if path is None:
        path = find_config(start or Path.cwd())
    if path is None:
        raise FileNotFoundError(
            "no godot-loop.toml found in cwd or any parent directory"
        )
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    root = path.parent

    project = raw.get("project", {})
    project_path_raw = project.get("path")
    if not project_path_raw:
        raise ValueError(f"{path}: [project].path is required")
    project_path = (root / project_path_raw).resolve()

    env_file_raw = project.get("env_file")
    env_file = (root / env_file_raw).resolve() if env_file_raw else None

    health_raw = raw.get("health", {})
    health = HealthConfig(
        url=health_raw.get("url"),
        timeout_seconds=float(health_raw.get("timeout_seconds", 5.0)),
    )

    e2e_raw = raw.get("e2e", {})
    e2e = E2EConfig(
        launch_args=list(e2e_raw.get("launch_args", [])),
        log_markers=list(e2e_raw.get("log_markers", [])),
        screenshot_after_ms=int(e2e_raw.get("screenshot_after_ms", 12000)),
        timeout_seconds=int(e2e_raw.get("timeout_seconds", 60)),
        headless=bool(e2e_raw.get("headless", False)),
    )

    udt_raw = raw.get("user_dir_tag", {})
    udt = UserDirTagConfig(
        strategy=udt_raw.get("strategy", "worktree-basename"),
        prefix=udt_raw.get("prefix", "loop"),
        fixed=udt_raw.get("fixed"),
    )

    hooks_raw = raw.get("hooks", {})
    hooks = HooksConfig(pre_launch=hooks_raw.get("pre_launch"))

    smokes_raw = raw.get("smokes", {})
    smokes = SmokesConfig(
        path=smokes_raw.get("path", "scripts/dev"),
        pattern=smokes_raw.get("pattern", "*_smoke.gd"),
    )

    inspect_port = raw.get("inspect_port")

    return LoopConfig(
        project_path=project_path,
        env_file=env_file,
        health=health,
        e2e=e2e,
        user_dir_tag=udt,
        hooks=hooks,
        smokes=smokes,
        inspect_port=int(inspect_port) if inspect_port is not None else None,
        config_path=path,
        config_root=root,
    )


def resolve_user_dir_tag(cfg: LoopConfig, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if cfg.user_dir_tag.strategy == "fixed":
        if not cfg.user_dir_tag.fixed:
            raise ValueError("user_dir_tag.strategy=fixed requires user_dir_tag.fixed")
        return cfg.user_dir_tag.fixed
    from .utils import worktree_basename
    base = worktree_basename(cfg.config_root)
    return f"{cfg.user_dir_tag.prefix}-{base}"


def resolve_inspect_port(cfg: LoopConfig, env: dict[str, Any], explicit: int | None = None) -> int | None:
    """Pick an inspector port: explicit > config > env BACKEND_PORT+100."""
    if explicit:
        return explicit
    if cfg.inspect_port:
        return cfg.inspect_port
    backend_port_raw = env.get("BACKEND_PORT")
    if backend_port_raw:
        try:
            return int(backend_port_raw) + 100
        except ValueError:
            return None
    return None
