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
