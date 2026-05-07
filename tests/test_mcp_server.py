"""Tests for the MCP server inspect tool — content-type-aware response handling."""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")  # mcp_server requires the optional [mcp] extra

from godot_loop import mcp_server  # noqa: E402


class _FakeResponse:
    def __init__(self, *, body: str, content_type: str, status_code: int = 200) -> None:
        self.text = body
        self._body = body
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}

    def json(self) -> dict:
        import json

        return json.loads(self._body)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_inspector(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> list[str]:
    """Stub _inspector_base + requests.get; return the list that captures urls hit."""
    urls: list[str] = []

    def fake_get(url: str, timeout: float = 10.0) -> _FakeResponse:
        urls.append(url)
        return response

    monkeypatch.setattr(mcp_server, "_inspector_base", lambda: "http://127.0.0.1:9999")
    monkeypatch.setattr(mcp_server.requests, "get", fake_get)
    return urls


def test_inspect_parses_application_json(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(body='{"available": true, "cards": []}', content_type="application/json")
    urls = _patch_inspector(monkeypatch, response)

    result = mcp_server._do_inspect("/cards")

    assert urls == ["http://127.0.0.1:9999/cards"]
    assert result == {"available": True, "cards": []}


def test_inspect_wraps_text_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    """/healthz returns 'ok\\n' as text/plain — must not blow up JSON parser."""
    response = _FakeResponse(body="ok\n", content_type="text/plain")
    _patch_inspector(monkeypatch, response)

    result = mcp_server._do_inspect("/healthz")

    assert result == {"text": "ok\n", "content_type": "text/plain"}


def test_inspect_handles_content_type_with_charset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server may include `; charset=utf-8` after the content type — strip before matching."""
    response = _FakeResponse(
        body='{"ok": 1}',
        content_type="application/json; charset=utf-8",
    )
    _patch_inspector(monkeypatch, response)

    result = mcp_server._do_inspect("/scene")

    assert result == {"ok": 1}


def test_inspect_unknown_content_type_falls_back_to_text(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(body="raw", content_type="")
    _patch_inspector(monkeypatch, response)

    result = mcp_server._do_inspect("/weird")

    assert result == {"text": "raw", "content_type": "unknown"}


def test_inspect_normalizes_endpoint_without_leading_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(body='{"ok": 1}', content_type="application/json")
    urls = _patch_inspector(monkeypatch, response)

    mcp_server._do_inspect("scene")

    assert urls == ["http://127.0.0.1:9999/scene"]
