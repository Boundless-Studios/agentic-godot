"""Command-line entry point for godot-loop."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from . import runners


def _add_config_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to godot-loop.toml (default: search cwd + parents)",
    )


def _load(args: argparse.Namespace):
    return load_config(start=Path.cwd(), explicit=args.config)


def cmd_run_e2e(args: argparse.Namespace) -> int:
    cfg = _load(args)
    result = runners.run_e2e(
        cfg,
        api_base=args.api_base,
        headless=args.headless,
        screenshot_path=args.screenshot_path,
        keep_output=args.keep_output,
        extra_args=args.extra,
    )
    if not result.bootstrap_ok:
        print("FAIL: not all log markers seen — bootstrap incomplete", file=sys.stderr)
        return 1
    if result.exit_code not in (0, 124):
        # 124 from a timeout right after success markers is fine; surface
        # actual godot crashes.
        print(f"FAIL: godot exit={result.exit_code}", file=sys.stderr)
        return result.exit_code
    print("==> godot-loop run e2e: ok")
    return 0


def cmd_run_smoke(args: argparse.Namespace) -> int:
    cfg = _load(args)
    return runners.run_smoke(cfg, args.smoke_path, timeout_seconds=args.timeout)


def cmd_inspect(args: argparse.Namespace) -> int:
    cfg = _load(args)
    return runners.inspect(
        cfg,
        port=args.port,
        endpoint=args.endpoint,
        save_to=args.save_to,
    )


def cmd_trace(args: argparse.Namespace) -> int:
    cfg = _load(args)
    return runners.trace(
        cfg,
        port=args.port,
        interval=args.interval,
        endpoints=args.endpoint or ("/cards", "/text"),
    )


def cmd_input(args: argparse.Namespace) -> int:
    cfg = _load(args)
    payload: dict = {"type": args.event_type}
    if args.event_type == "mouse_button":
        payload["button"] = args.button
        payload["pressed"] = args.pressed
        if args.x is not None:
            payload["x"] = args.x
        if args.y is not None:
            payload["y"] = args.y
    elif args.event_type == "mouse_motion":
        payload["x"] = args.x or 0
        payload["y"] = args.y or 0
    elif args.event_type == "key":
        payload["keycode"] = args.keycode
        payload["pressed"] = args.pressed
        if args.shift:
            payload["shift"] = True
        if args.ctrl:
            payload["ctrl"] = True
        if args.alt:
            payload["alt"] = True
        if args.meta:
            payload["meta"] = True
    return runners.send_input(cfg, port=args.port, payload=payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="godot-loop", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run a smoke or e2e against the project")
    run_sub = run.add_subparsers(dest="run_cmd", required=True)

    p_e2e = run_sub.add_parser("e2e", help="Boot client headless w/ smoke flags + assert markers")
    _add_config_arg(p_e2e)
    p_e2e.add_argument("--api-base", default=None, help="Override API base URL")
    p_e2e.add_argument("--headless", action="store_true", default=None, help="Force --headless (no screenshot)")
    p_e2e.add_argument("--screenshot-path", type=Path, default=None, help="Where to copy the captured PNG")
    p_e2e.add_argument("--keep-output", action="store_true", help="Don't delete the temp work dir")
    p_e2e.add_argument("--extra", nargs=argparse.REMAINDER, help="Additional flags to pass after --")
    p_e2e.set_defaults(func=cmd_run_e2e)

    p_smoke = run_sub.add_parser("smoke", help="Run a single *_smoke.gd file headlessly")
    _add_config_arg(p_smoke)
    p_smoke.add_argument("smoke_path", type=Path)
    p_smoke.add_argument("--timeout", type=int, default=60)
    p_smoke.set_defaults(func=cmd_run_smoke)

    p_inspect = sub.add_parser("inspect", help="GET an endpoint from a running RuntimeInspectorServer")
    _add_config_arg(p_inspect)
    p_inspect.add_argument("--port", type=int, default=None, help="Inspector port (default: BACKEND_PORT+100)")
    p_inspect.add_argument("--endpoint", default="/scene", help="Endpoint path (default: /scene)")
    p_inspect.add_argument("--save-to", type=Path, default=None, help="When fetching /screenshot.png, write here")
    p_inspect.set_defaults(func=cmd_inspect)

    p_trace = sub.add_parser("trace", help="Poll inspector endpoints, print on change")
    _add_config_arg(p_trace)
    p_trace.add_argument("--port", type=int, default=None)
    p_trace.add_argument("--interval", type=float, default=1.0)
    p_trace.add_argument("--endpoint", action="append", help="Endpoint(s) to poll (repeatable)")
    p_trace.set_defaults(func=cmd_trace)

    p_input = sub.add_parser("input", help="POST an InputEvent to the inspector")
    _add_config_arg(p_input)
    p_input.add_argument("--port", type=int, default=None)
    p_input.add_argument("event_type", choices=["mouse_button", "mouse_motion", "key"])
    p_input.add_argument("--button", default="left")
    p_input.add_argument("--x", type=int, default=None)
    p_input.add_argument("--y", type=int, default=None)
    p_input.add_argument("--pressed", action="store_true", default=True)
    p_input.add_argument("--released", dest="pressed", action="store_false")
    p_input.add_argument("--keycode", default=0)
    p_input.add_argument("--shift", action="store_true")
    p_input.add_argument("--ctrl", action="store_true")
    p_input.add_argument("--alt", action="store_true")
    p_input.add_argument("--meta", action="store_true")
    p_input.set_defaults(func=cmd_input)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
