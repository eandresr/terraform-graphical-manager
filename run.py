#!/usr/bin/env python3
"""
Terraform Graphical Manager — Entry Point
Run with:  python run.py [--port PORT]
"""
import argparse
import os
import sys

# Anchor the project root **before** any app import so that app.py can locate
# templates/ and static/ correctly even when installed non-editably.
os.environ.setdefault("TGM_ROOT_DIR", os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.app import create_app, socketio  # noqa: E402

app = create_app()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Terraform Graphical Manager",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 5005)),
        help="Port to listen on (default: 5005, or $PORT env var)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=os.environ.get("DEBUG", "false").lower() == "true",
        help="Enable debug mode",
    )
    return parser


def main(argv: list = None) -> None:
    args = _build_parser().parse_args(argv)
    print("\n  Terraform Graphical Manager")
    print(f"  Running at: http://0.0.0.0:{args.port}\n")
    socketio.run(
        app,
        host="0.0.0.0",
        port=args.port,
        debug=args.debug,
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    main()
