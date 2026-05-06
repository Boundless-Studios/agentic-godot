from __future__ import annotations

from pathlib import Path

import pytest

from godot_loop.utils import (
    find_godot_binary,
    grep_any,
    normalize_api_base,
    source_env_file,
    worktree_basename,
)


def test_normalize_api_base_idempotent() -> None:
    assert normalize_api_base("http://127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_normalize_api_base_strips_localhost_with_port() -> None:
    assert normalize_api_base("http://localhost:8000/api") == "http://127.0.0.1:8000/api"


def test_normalize_api_base_strips_localhost_with_path() -> None:
    assert normalize_api_base("http://localhost/health") == "http://127.0.0.1/health"


def test_normalize_api_base_strips_bare_localhost() -> None:
    assert normalize_api_base("https://localhost") == "https://127.0.0.1"


def test_normalize_api_base_leaves_other_hosts_alone() -> None:
    assert normalize_api_base("https://example.com:9000") == "https://example.com:9000"


def test_normalize_api_base_handles_empty() -> None:
    assert normalize_api_base("") == ""
    assert normalize_api_base("   ") == ""


def test_worktree_basename_sanitizes(tmp_path: Path) -> None:
    target = tmp_path / "branch with spaces!@#"
    target.mkdir()
    out = worktree_basename(target)
    assert "!" not in out
    assert "@" not in out
    assert " " not in out
    # Letters and digits and - and _ survive.
    assert all(c.isalnum() or c in "-_" for c in out)


def test_source_env_file_basic(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("FOO=bar\nBAZ=\"qux\"\n# comment\n\nEMPTY=\n")
    out = source_env_file(p)
    assert out == {"FOO": "bar", "BAZ": "qux", "EMPTY": ""}


def test_source_env_file_missing_returns_empty(tmp_path: Path) -> None:
    assert source_env_file(tmp_path / "nope") == {}


def test_grep_any_finds_present_strings(tmp_path: Path) -> None:
    p = tmp_path / "log.txt"
    p.write_text("line one\nbootstrap_succeeded\nline three\n")
    out = grep_any(p, ["bootstrap_succeeded", "ready"])
    assert out == {"bootstrap_succeeded": True, "ready": False}


def test_grep_any_handles_missing_file(tmp_path: Path) -> None:
    out = grep_any(tmp_path / "no", ["x", "y"])
    assert out == {"x": False, "y": False}


def test_find_godot_binary_honours_env(tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "godot"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("GODOT", str(fake))
    assert find_godot_binary() == str(fake)


def test_find_godot_binary_rejects_nonexecutable(tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "godot"
    fake.write_text("data")  # not chmod +x
    monkeypatch.setenv("GODOT", str(fake))
    with pytest.raises(FileNotFoundError):
        find_godot_binary()
