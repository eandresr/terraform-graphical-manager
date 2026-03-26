"""
Terraform Graphical Manager — Flask Application Factory
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask  # noqa: E402
from flask_socketio import SocketIO  # noqa: E402

socketio = SocketIO()
execution_queue = None


def _find_root_dir() -> str:
    """Locate the project root that contains templates/ and static/.

    Works for both editable installs (pip install -e .) and non-editable
    installs (pip install .) by trying, in order:
    1. TGM_ROOT_DIR env var  — set by run.py / tgm CLI before import
    2. Current working directory — when running 'tgm start' from project root
    3. Walking up from __file__  — for editable installs
    4. Original two-levels-up fallback
    """
    env_root = os.environ.get("TGM_ROOT_DIR")
    if env_root and os.path.isdir(os.path.join(env_root, "templates")):
        return env_root

    cwd = os.getcwd()
    if os.path.isdir(os.path.join(cwd, "templates")):
        return cwd

    candidate = os.path.abspath(__file__)
    for _ in range(8):
        candidate = os.path.dirname(candidate)
        if os.path.isdir(os.path.join(candidate, "templates")):
            return candidate

    # Original fallback — works for editable installs
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_ROOT_DIR = _find_root_dir()


def create_app(config_path: str = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder=os.path.join(_ROOT_DIR, "templates"),
        static_folder=os.path.join(_ROOT_DIR, "static"),
    )

    app.secret_key = os.environ.get(
        "SECRET_KEY", "terraform-graphical-manager-dev-secret-key"
    )

    # Load application config
    from app.config import Config

    cfg_path = config_path or os.environ.get(
        "TFG_CONFIG", os.path.join(_ROOT_DIR, "config", "tfg.conf")
    )
    config = Config(cfg_path)
    app.config["TFG_CONFIG"] = config

    # Initialize execution queue
    global execution_queue
    from app.execution_queue import ExecutionQueue

    execution_queue = ExecutionQueue(
        max_workers=config.max_concurrent_executions,
        socketio_instance=socketio,
    )
    execution_queue.start()
    app.config["EXECUTION_QUEUE"] = execution_queue

    # Context processor — inject workspace tree into every template
    @app.context_processor
    def inject_globals():
        from app.workspace_scanner import WorkspaceScanner

        scanner = WorkspaceScanner(config.repos_root)
        return {
            "workspace_tree": scanner.get_tree(),
            "flat_workspaces": scanner.get_flat_list(),
            "repos_root": config.repos_root,
            "site_name": config.site_name,
            "repo_url": config.repo_url,
            "config": config,
        }

    # Register blueprints
    from app.routes.workspace_routes import workspace_bp
    from app.routes.execution_routes import execution_bp
    from app.routes.api_routes import api_bp
    from app.routes.settings_routes import settings_bp
    from app.routes.auth_routes import auth_bp

    app.register_blueprint(workspace_bp)
    app.register_blueprint(execution_bp, url_prefix="/executions")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(settings_bp)
    app.register_blueprint(auth_bp)

    # ------------------------------------------------------------------
    # Portal lock — gate ALL routes when a password is configured
    # ------------------------------------------------------------------
    @app.before_request
    def _check_portal_lock():
        from flask import jsonify, redirect, request, session, url_for

        pwd_hash = app.config["TFG_CONFIG"].lock_password_hash
        if not pwd_hash:
            return  # portal is unlocked

        # Always allow auth endpoints and static assets
        if request.endpoint in ("auth.login", "auth.login_post", "auth.logout", "static"):
            return

        # API routes: accept a Bearer token OR an authenticated browser session
        if request.path.startswith("/api/") or request.path.startswith("/executions"):
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                from app.auth import verify_api_token
                token = auth_header[7:]
                if verify_api_token(token, pwd_hash, app.secret_key):
                    return
            # Also accept session cookies (browser-originated fetch calls)
            if session.get("tgm_authenticated"):
                return
            return jsonify({
                "error": "Unauthorized",
                "hint": "Add header: Authorization: Bearer <api_token>",
            }), 401

        # Browser: check session
        if session.get("tgm_authenticated"):
            return

        return redirect(url_for("auth.login", next=request.path))

    # Initialize Socket.IO (threading mode for simplicity)
    socketio.init_app(app, cors_allowed_origins="*", async_mode="threading")

    return app
