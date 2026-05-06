from __future__ import annotations

import argparse

import pytest

from godot_loop.cli import build_parser


def _parse(args: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(args)


def test_run_e2e_flags() -> None:
    ns = _parse(["run", "e2e", "--api-base", "http://x:1", "--headless", "--keep-output"])
    assert ns.cmd == "run"
    assert ns.run_cmd == "e2e"
    assert ns.api_base == "http://x:1"
    assert ns.headless is True
    assert ns.keep_output is True


def test_run_smoke_requires_path() -> None:
    with pytest.raises(SystemExit):
        _parse(["run", "smoke"])


def test_run_smokes_no_path_arg() -> None:
    ns = _parse(["run", "smokes", "--timeout", "30"])
    assert ns.run_cmd == "smokes"
    assert ns.timeout == 30


def test_inspect_defaults() -> None:
    ns = _parse(["inspect"])
    assert ns.cmd == "inspect"
    assert ns.endpoint == "/scene"
    assert ns.port is None


def test_inspect_with_port_and_endpoint() -> None:
    ns = _parse(["inspect", "--port", "8765", "--endpoint", "/text"])
    assert ns.port == 8765
    assert ns.endpoint == "/text"


def test_trace_endpoints_repeatable() -> None:
    ns = _parse(["trace", "--endpoint", "/scene", "--endpoint", "/text"])
    assert ns.endpoint == ["/scene", "/text"]


def test_input_mouse_button() -> None:
    ns = _parse(["input", "mouse_button", "--button", "left", "--x", "10", "--y", "20"])
    assert ns.event_type == "mouse_button"
    assert ns.button == "left"
    assert ns.x == 10
    assert ns.y == 20
    assert ns.pressed is True


def test_input_released_flag() -> None:
    ns = _parse(["input", "mouse_button", "--released"])
    assert ns.pressed is False


def test_input_key_modifiers() -> None:
    ns = _parse(["input", "key", "--keycode", "A", "--shift", "--ctrl"])
    assert ns.event_type == "key"
    assert ns.keycode == "A"
    assert ns.shift is True
    assert ns.ctrl is True
    assert ns.alt is False


def test_rebuild_class_cache_default_quit_after() -> None:
    ns = _parse(["rebuild-class-cache"])
    assert ns.cmd == "rebuild-class-cache"
    assert ns.quit_after == 200


def test_unknown_subcommand_errors() -> None:
    with pytest.raises(SystemExit):
        _parse(["nope"])
