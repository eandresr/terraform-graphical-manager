"""
Auth Routes — login / logout for the portal lock feature.
"""
from flask import (
    Blueprint, current_app, render_template, request,
    redirect, url_for, session, flash,
)

from app.auth import check_password

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET"])
def login():
    if session.get("tgm_authenticated"):
        return redirect(url_for("workspace.dashboard"))
    next_url = request.args.get("next", "")
    return render_template("login.html", next_url=next_url)


@auth_bp.route("/login", methods=["POST"])
def login_post():
    config = current_app.config["TFG_CONFIG"]
    pwd_hash = config.lock_password_hash
    plain = request.form.get("password", "")
    next_url = request.form.get("next_url", "")

    if check_password(plain, pwd_hash):
        session["tgm_authenticated"] = True
        session.permanent = False
        safe_next = next_url if (next_url and next_url.startswith("/")) else None
        target = safe_next or url_for("workspace.dashboard")
        return redirect(target)

    flash("Incorrect password.", "error")
    return render_template("login.html", next_url=next_url), 401


@auth_bp.route("/logout")
def logout():
    session.pop("tgm_authenticated", None)
    return redirect(url_for("auth.login"))
