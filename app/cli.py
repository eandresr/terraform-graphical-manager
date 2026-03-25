"""
Terraform Graphical Manager — CLI entry point.

Usage examples::

    tgm --help
    tgm start --help
    tgm start
    tgm start --port 5000
    tgm start --port 5000 --host 127.0.0.1
    tgm start --port 5000 --debug
"""
import argparse
import os


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tgm",
        description=(
            "Terraform Graphical Manager — a local UI for managing "
            "Terraform workspaces."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  tgm start                      # start on default port 5005\n"
            "  tgm start --port 5000          # start on port 5000\n"
            "  tgm start --port 8080 --debug  # start with debug mode\n"
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        title="available commands",
    )
    subparsers.required = True

    _add_start_command(subparsers)

    return parser


def _add_start_command(subparsers) -> None:
    """Register the 'start' subcommand."""
    start = subparsers.add_parser(
        "start",
        help="Start the Terraform Graphical Manager web server.",
        description=(
            "Launch the Flask/SocketIO web server.\n\n"
            "Once started, open http://localhost:<port> in your browser.\n"
            "Press Ctrl+C to stop the server."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables:\n"
            "  PORT   Default port when --port is not provided (default: 5005)\n"
            "  HOST   Default host when --host is not provided (default: 0.0.0.0)\n"
            "  DEBUG  Set to 'true' to enable debug mode\n"
        ),
    )
    start.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 5005)),
        metavar="PORT",
        help="Port to listen on (default: 5005, or $PORT env var)",
    )
    start.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        metavar="HOST",
        help="Network interface to bind to (default: 0.0.0.0)",
    )
    start.add_argument(
        "--debug",
        action="store_true",
        default=os.environ.get("DEBUG", "false").lower() == "true",
        help="Enable Flask debug mode",
    )
    start.set_defaults(func=_cmd_start)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_start(args: argparse.Namespace) -> None:
    """Handle the 'tgm start' command."""
    # Lazy import — keeps 'tgm --help' fast and free of Flask side-effects.
    from app.app import create_app, socketio

    app = create_app()

    print("\n  Terraform Graphical Manager")
    print(f"  Running at: http://{args.host}:{args.port}")
    if args.debug:
        print("  Debug mode: ON")
    print()

    socketio.run(
        app,
        host=args.host,
        port=args.port,
        debug=args.debug,
        allow_unsafe_werkzeug=True,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list = None) -> None:
    """
    CLI entry point registered as the 'tgm' console script.

    Called by the 'tgm' command after ``pip install .`` or
    ``pip install -e .``.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
