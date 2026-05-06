from __future__ import annotations

from pathlib import Path

import pytest

from godot_loop.config import (
    LoopConfig,
    UserDirTagConfig,
    find_config,
    load_config,
    resolve_inspect_port,
    resolve_user_dir_tag,
)


def _write(tmp: Path, name: str, content: str) -> Path:
    p = tmp / name
    p.write_text(content)
    return p


def test_minimum_config_loads(tmp_path: Path) -> None:
    (tmp_path / "game").mkdir()
    cfg_path = _write(tmp_path, "godot-loop.toml", '[project]\npath = "game"\n')
    cfg = load_config(explicit=cfg_path)
    assert cfg.project_path == (tmp_path / "game").resolve()
    assert cfg.health.url is None
    assert cfg.e2e.launch_args == []
    assert cfg.e2e.log_markers == []
    assert cfg.smokes.path == "scripts/dev"
    assert cfg.smokes.pattern == "*_smoke.gd"


def test_full_config_round_trip(tmp_path: Path) -> None:
    (tmp_path / "game").mkdir()
    (tmp_path / ".env").write_text("BACKEND_PORT=9000\n")
    cfg_path = _write(tmp_path, "godot-loop.toml", """
inspect_port = 8765

[project]
path = "game"
env_file = ".env"

[health]
url = "http://127.0.0.1:9000/health"
timeout_seconds = 7

[e2e]
launch_args = ["--exit-after-bootstrap"]
log_markers = ["ready"]
screenshot_after_ms = 5000
timeout_seconds = 90
headless = true

[user_dir_tag]
strategy = "fixed"
prefix = "ignored"
fixed = "my-tag"

[hooks]
pre_launch = "scripts/x.sh"

[smokes]
path = "tests"
pattern = "*test.gd"
""")
    cfg = load_config(explicit=cfg_path)
    assert cfg.health.timeout_seconds == 7.0
    assert cfg.e2e.launch_args == ["--exit-after-bootstrap"]
    assert cfg.e2e.log_markers == ["ready"]
    assert cfg.e2e.screenshot_after_ms == 5000
    assert cfg.e2e.headless is True
    assert cfg.user_dir_tag.strategy == "fixed"
    assert cfg.user_dir_tag.fixed == "my-tag"
    assert cfg.hooks.pre_launch == "scripts/x.sh"
    assert cfg.smokes.path == "tests"
    assert cfg.smokes.pattern == "*test.gd"
    assert cfg.inspect_port == 8765


def test_missing_project_path_raises(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, "godot-loop.toml", "[project]\n")
    with pytest.raises(ValueError, match="path is required"):
        load_config(explicit=cfg_path)


def test_find_config_walks_up(tmp_path: Path) -> None:
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    cfg = _write(tmp_path / "a", "godot-loop.toml", '[project]\npath = "."\n')
    found = find_config(tmp_path / "a" / "b" / "c")
    assert found == cfg


def test_find_config_returns_none_when_missing(tmp_path: Path) -> None:
    assert find_config(tmp_path) is None


def test_resolve_user_dir_tag_fixed() -> None:
    cfg = LoopConfig(
        project_path=Path("/x"),
        user_dir_tag=UserDirTagConfig(strategy="fixed", fixed="alpha"),
    )
    assert resolve_user_dir_tag(cfg) == "alpha"


def test_resolve_user_dir_tag_fixed_requires_fixed_value() -> None:
    cfg = LoopConfig(
        project_path=Path("/x"),
        user_dir_tag=UserDirTagConfig(strategy="fixed"),
    )
    with pytest.raises(ValueError):
        resolve_user_dir_tag(cfg)


def test_resolve_user_dir_tag_worktree_basename(tmp_path: Path) -> None:
    target = tmp_path / "weird name!"
    target.mkdir()
    cfg = LoopConfig(
        project_path=Path("/x"),
        config_root=target,
    )
    out = resolve_user_dir_tag(cfg)
    # Sanitized: non [A-Za-z0-9_-] becomes _
    assert out.startswith("loop-")
    assert "!" not in out
    assert " " not in out


def test_resolve_inspect_port_priority() -> None:
    cfg = LoopConfig(project_path=Path("/x"), inspect_port=4000)
    # Explicit beats config beats env.
    assert resolve_inspect_port(cfg, {"BACKEND_PORT": "8000"}, explicit=9999) == 9999
    assert resolve_inspect_port(cfg, {"BACKEND_PORT": "8000"}) == 4000
    cfg2 = LoopConfig(project_path=Path("/x"))
    assert resolve_inspect_port(cfg2, {"BACKEND_PORT": "8000"}) == 8100
    assert resolve_inspect_port(cfg2, {}) is None


def test_resolve_user_dir_tag_explicit_override(tmp_path: Path) -> None:
    cfg = LoopConfig(project_path=Path("/x"), config_root=tmp_path)
    assert resolve_user_dir_tag(cfg, explicit="custom") == "custom"
