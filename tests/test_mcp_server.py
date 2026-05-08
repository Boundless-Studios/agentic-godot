"""Tests for the MCP server inspect tool — JSON-or-text response handling."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp")  # mcp_server requires the optional [mcp] extra

from godot_loop import mcp_server  # noqa: E402


class _FakeResponse:
    """Minimal stand-in for requests.Response — body is JSON-parsed iff valid."""

    def __init__(self, body: str, *, status_code: int = 200) -> None:
        self.text = body
        self._body = body
        self.status_code = status_code

    def json(self) -> object:
        return json.loads(self._body)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_inspector(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> list[str]:
    """Stub _inspector_base + requests.get; return list capturing urls hit."""
    urls: list[str] = []

    def fake_get(url: str, timeout: float = 10.0) -> _FakeResponse:
        urls.append(url)
        return response

    monkeypatch.setattr(mcp_server, "_inspector_base", lambda: "http://127.0.0.1:9999")
    monkeypatch.setattr(mcp_server.requests, "get", fake_get)
    return urls


def test_inspect_returns_parsed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = _patch_inspector(monkeypatch, _FakeResponse('{"available": true, "cards": []}'))

    result = mcp_server._do_inspect("/cards")

    assert urls == ["http://127.0.0.1:9999/cards"]
    assert result == {"available": True, "cards": []}


def test_inspect_falls_back_to_text_on_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """/healthz returns 'ok\\n' — must not blow up the tool."""
    _patch_inspector(monkeypatch, _FakeResponse("ok\n"))

    assert mcp_server._do_inspect("/healthz") == {"text": "ok\n"}


def test_inspect_normalizes_endpoint_without_leading_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = _patch_inspector(monkeypatch, _FakeResponse('{"ok": 1}'))

    mcp_server._do_inspect("scene")

    assert urls == ["http://127.0.0.1:9999/scene"]


# ---------------------------------------------------------------------------
# BOU-891: editor-less MCP harness — tests for the new tool surface.
# These tests are written FIRST (RED) and prove the tools wire to the right
# inspector routes / subprocess args before any implementation lands.
# ---------------------------------------------------------------------------


def test_press_button_hits_inspector_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """press_button(node_path=...) GETs /press_button?path=<urlencoded path>."""
    urls = _patch_inspector(
        monkeypatch,
        _FakeResponse('{"ok": true, "node": "/root/Main/Btn", "method": "signal"}'),
    )

    result = mcp_server.press_button(node_path="/root/Main/Btn")

    assert len(urls) == 1
    assert urls[0].startswith("http://127.0.0.1:9999/press_button")
    # NodePath must be URL-encoded so '/' survives as %2F
    assert "path=%2Froot%2FMain%2FBtn" in urls[0]
    assert result == {"ok": True, "node": "/root/Main/Btn", "method": "signal"}


def test_get_state_hits_state_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_state() GETs /state — the project-registered inspector provider."""
    urls = _patch_inspector(
        monkeypatch,
        _FakeResponse('{"available": true, "round": 2, "active_combatant_id": "c1"}'),
    )

    result = mcp_server.get_state()

    assert urls == ["http://127.0.0.1:9999/state"]
    assert result["round"] == 2
    assert result["active_combatant_id"] == "c1"


def test_get_scene_tree_hits_scene_tree_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_scene_tree() GETs /scene_tree — the new built-in route rooted at current_scene."""
    urls = _patch_inspector(
        monkeypatch,
        _FakeResponse('{"available": true, "root": {"name": "CombatScene", "type": "Node"}}'),
    )

    result = mcp_server.get_scene_tree()

    assert urls == ["http://127.0.0.1:9999/scene_tree"]
    assert result["root"]["name"] == "CombatScene"


def test_wait_for_route_returns_once_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """wait_for_route polls until status==200, then returns ok=true."""
    monkeypatch.setattr(mcp_server, "_inspector_base", lambda: "http://127.0.0.1:9999")
    calls: list[str] = []

    # First two probes raise ConnectionError; third returns 200.
    responses = [
        ConnectionError("refused"),
        ConnectionError("refused"),
        _FakeResponse("ok\n"),
    ]

    def fake_get(url: str, timeout: float = 10.0) -> _FakeResponse:
        calls.append(url)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(mcp_server.requests, "get", fake_get)

    result = mcp_server.wait_for_route(
        path="/healthz",
        timeout_seconds=5.0,
        interval_seconds=0.01,
    )

    assert result["ok"] is True
    assert result["attempts"] >= 3
    assert all(u == "http://127.0.0.1:9999/healthz" for u in calls)


def test_wait_for_route_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """wait_for_route returns ok=false when the route never goes healthy."""
    monkeypatch.setattr(mcp_server, "_inspector_base", lambda: "http://127.0.0.1:9999")

    def always_fail(url: str, timeout: float = 10.0) -> _FakeResponse:
        raise ConnectionError("refused")

    monkeypatch.setattr(mcp_server.requests, "get", always_fail)

    result = mcp_server.wait_for_route(
        path="/state",
        timeout_seconds=0.1,
        interval_seconds=0.01,
    )

    assert result["ok"] is False
    assert result["attempts"] >= 1
    assert "last_error" in result or "last_status" in result


class _FakePopen:
    """Minimal subprocess.Popen stand-in used to verify the godot command line."""

    def __init__(self, argv: list[str], **_kwargs: object) -> None:
        self.argv = argv
        self.pid = 12345
        self._terminated = False
        self._wait_returns = 0

    def poll(self) -> int | None:
        return None if not self._terminated else self._wait_returns

    def terminate(self) -> None:
        self._terminated = True

    def kill(self) -> None:
        self._terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self._terminated = True
        return self._wait_returns


def test_launch_runtime_invokes_godot_with_inspect_port_and_api_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """launch_runtime forks 'godot --path X -- --inspect-port=N --api-base=...' and waits for /healthz."""
    captured: dict[str, object] = {}

    def fake_popen(argv: list[str], **kwargs: object) -> _FakePopen:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakePopen(argv, **kwargs)

    monkeypatch.setattr(mcp_server.subprocess, "Popen", fake_popen)
    # /healthz becomes reachable immediately
    monkeypatch.setattr(
        mcp_server,
        "wait_for_route",
        lambda path, timeout_seconds=30.0, interval_seconds=0.2: {
            "ok": True,
            "elapsed_seconds": 0.01,
            "attempts": 1,
            "last_status": 200,
        },
    )

    result = mcp_server.launch_runtime(
        repo_path="/some/godot/project",
        api_base="http://127.0.0.1:8090",
        mode="combat",
        inspect_port=9876,
    )

    argv = captured["argv"]
    assert isinstance(argv, list)
    # First element is the godot binary (path or "godot")
    assert "godot" in argv[0] or argv[0].endswith("godot")
    assert "--path" in argv
    assert "/some/godot/project" in argv
    # Project-side args follow `--`
    sep = argv.index("--")
    project_args = argv[sep + 1 :]
    assert any(a.startswith("--inspect-port=") and a.endswith("9876") for a in project_args)
    assert any(a.startswith("--api-base=") for a in project_args)
    assert any(a.startswith("--mode=") and a.endswith("combat") for a in project_args)

    assert result["pid"] == 12345
    assert result["healthy"] is True
    assert result["inspect_port"] == 9876


def test_kill_runtime_terminates_launched_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """kill_runtime terminates the previously-launched process."""
    fake = _FakePopen(["godot", "--path", "/x"], )

    def fake_popen(argv: list[str], **kwargs: object) -> _FakePopen:
        return fake

    monkeypatch.setattr(mcp_server.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        mcp_server,
        "wait_for_route",
        lambda path, timeout_seconds=30.0, interval_seconds=0.2: {
            "ok": True, "elapsed_seconds": 0.0, "attempts": 1, "last_status": 200,
        },
    )

    mcp_server.launch_runtime(repo_path="/x", inspect_port=9999)
    result = mcp_server.kill_runtime()

    assert fake._terminated is True
    assert result["ok"] is True
    assert result["pid"] == 12345
