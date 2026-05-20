from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_INSPECTOR = ROOT / "addon" / "godot_loop" / "RuntimeInspectorServer.gd"


def test_query_depth_accepts_zero_as_explicit_root_only_depth() -> None:
    source = RUNTIME_INSPECTOR.read_text()
    query_depth = source.split("func _query_depth", 1)[1].split("\n\n", 1)[0]

    assert "raw.is_valid_int()" in query_depth
    assert "parsed < 0" in query_depth
    assert "parsed <= 0" not in query_depth
