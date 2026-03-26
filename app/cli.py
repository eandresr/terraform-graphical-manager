"""
Terraform Graphical Manager — CLI entry point.

Usage examples::

    tgm --help
    tgm start --help
    tgm start
    tgm start --port 5000
    tgm start --port 5000 --host 127.0.0.1
    tgm start --port 5000 --debug
    tgm preview
    tgm preview --port 5005
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
    _add_preview_command(subparsers)

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


def _add_preview_command(subparsers) -> None:
    """Register the 'preview' subcommand."""
    preview = subparsers.add_parser(
        "preview",
        help="Start an ephemeral demo instance with a temporary workspace.",
        description=(
            "Launch an ephemeral Terraform Graphical Manager instance.\n\n"
            "Creates a temporary directory with an isolated config and workspace\n"
            "folder, starts the server, and removes everything on exit.\n"
            "Nothing is written to your home directory or project folder."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  tgm preview                    # ephemeral demo on port 5005\n"
            "  tgm preview --port 7000        # ephemeral demo on port 7000\n"
        ),
    )
    preview.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 5005)),
        metavar="PORT",
        help="Port to listen on (default: 5005, or $PORT env var)",
    )
    preview.add_argument(
        "--host",
        default=os.environ.get("HOST", "127.0.0.1"),
        metavar="HOST",
        help="Network interface to bind to (default: 127.0.0.1)",
    )
    preview.add_argument(
        "--debug",
        action="store_true",
        default=os.environ.get("DEBUG", "false").lower() == "true",
        help="Enable Flask debug mode",
    )
    preview.set_defaults(func=_cmd_preview)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_start(args: argparse.Namespace) -> None:
    """Handle the 'tgm start' command."""
    import os as _os
    # Anchor the project root so app.py can find templates/ and static/ when
    # installed non-editably (pip install .).  cwd is the project directory
    # when the user runs 'tgm start' from there; the env var wins if already set.
    if "TGM_ROOT_DIR" not in _os.environ:
        cwd = _os.getcwd()
        if _os.path.isdir(_os.path.join(cwd, "templates")):
            _os.environ["TGM_ROOT_DIR"] = cwd

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

def _cmd_preview(args: argparse.Namespace) -> None:
    """Handle the 'tgm preview' command — ephemeral demo instance."""
    import shutil
    import tempfile
    import os as _os

    # ── Locate project examples root ─────────────────────────────────
    # Works both when running from the repo and after a pip install.
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    _root_candidates = [
        _os.getcwd(),
        _os.path.normpath(_os.path.join(_pkg_dir, "..")),
    ]
    # When installed as a package the examples/ tree is shipped alongside.
    try:
        import importlib.resources as _ir
        with _ir.as_file(_ir.files("app").joinpath("..")) as _p:
            _root_candidates.append(str(_p))
    except Exception:
        pass

    _project_root = None
    for _r in _root_candidates:
        if (_os.path.isfile(_os.path.join(_r, "config", "tfg.conf.example"))
                and _os.path.isdir(_os.path.join(_r, "examples", "repos"))):
            _project_root = _os.path.normpath(_r)
            break

    if _project_root is None:
        print(
            "  [preview] ERROR: project root not found (need config/tfg.conf.example "
            "and examples/repos). Run from the project directory or reinstall."
        )
        raise SystemExit(1)

    example_conf = _os.path.join(_project_root, "config", "tfg.conf.example")
    example_repos = _os.path.join(_project_root, "examples", "repos")
    example_sentinel = _os.path.join(_project_root, "examples", "sentinel")

    # ── Create temp tree ─────────────────────────────────────────────
    # Layout:
    #   <tmpdir>/
    #     conf/tfg.conf           ← copy of example with paths rewritten
    #     workspaces/             ← copy of examples/repos (mutable demo data)
    #     sentinel/               ← copy of examples/sentinel (if present)
    tmpdir = tempfile.mkdtemp(prefix="tgm_preview_")
    conf_dir = _os.path.join(tmpdir, "conf")
    workspaces_dir = _os.path.join(tmpdir, "workspaces")
    sentinel_dir = _os.path.join(tmpdir, "sentinel")
    _os.makedirs(conf_dir)

    # Copy example repos so they are fully mutable inside the temp dir.
    shutil.copytree(example_repos, workspaces_dir)

    # Copy example sentinel policies only if they exist.
    has_sentinel = _os.path.isdir(example_sentinel)
    if has_sentinel:
        shutil.copytree(example_sentinel, sentinel_dir)

    # ── Rewrite config ───────────────────────────────────────────────
    with open(example_conf, encoding="utf-8") as _fh:
        conf_text = _fh.read()

    import re as _re

    _ws = workspaces_dir.replace("\\", "/")
    _sp = sentinel_dir.replace("\\", "/") if has_sentinel else ""

    conf_text = _re.sub(
        r"(?m)^(repos_root[ \t]*=[ \t]*).*",
        lambda m: m.group(1) + _ws,
        conf_text,
    )
    conf_text = _re.sub(
        r"(?m)^(global_policies[ \t]*=[ \t]*).*",
        lambda m: m.group(1) + _sp,
        conf_text,
    )
    # Leave versions_folder and cli_path empty (use whatever is on PATH).
    conf_text = _re.sub(
        r"(?m)^(versions_folder[ \t]*=[ \t]*).*",
        lambda m: m.group(1),
        conf_text,
    )
    conf_text = _re.sub(
        r"(?m)^(cli_path[ \t]*=[ \t]*).*",
        lambda m: m.group(1),
        conf_text,
    )

    conf_path = _os.path.join(conf_dir, "tfg.conf")
    with open(conf_path, "w", encoding="utf-8") as _fh:
        _fh.write(conf_text)

    # ── Point the app at the temp config ─────────────────────────────
    _os.environ["TFG_CONFIG"] = conf_path
    if "TGM_ROOT_DIR" not in _os.environ:
        if _os.path.isdir(_os.path.join(_os.getcwd(), "templates")):
            _os.environ["TGM_ROOT_DIR"] = _os.getcwd()

    # ── Banner ───────────────────────────────────────────────────────
    print("\n  Terraform Graphical Manager  [PREVIEW / ephemeral]")
    print(f"  Temp directory : {tmpdir}")
    print(f"  Config         : {conf_path}")
    print(f"  Workspaces     : {workspaces_dir}")
    if has_sentinel:
        print(f"  Sentinel       : {sentinel_dir}")
    print(f"  Running at     : http://{args.host}:{args.port}")
    print("  Press Ctrl+C to stop and clean up\n")

    # ── Start server (blocking) ───────────────────────────────────────
    from app.app import create_app, socketio

    app = create_app(config_path=conf_path)

    try:
        socketio.run(
            app,
            host=args.host,
            port=args.port,
            debug=args.debug,
            allow_unsafe_werkzeug=True,
        )
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
            print(f"\n  [preview] Cleaned up temp directory: {tmpdir}")
        except Exception:
            pass


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
