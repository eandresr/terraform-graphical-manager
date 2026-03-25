"""
Execution Routes — manage individual execution objects (detail view, cancel, logs).
"""
from flask import Blueprint, current_app, render_template, redirect, url_for, flash, jsonify

execution_bp = Blueprint("execution", __name__)


@execution_bp.route("/<execution_id>")
def execution_detail(execution_id: str):
    eq = current_app.config["EXECUTION_QUEUE"]
    execution = eq.get(execution_id)
    if execution is None:
        flash("Execution not found.", "error")
        return redirect(url_for("workspace.dashboard"))

    from app.plan_parser import parse_plan

    plan_summary = None
    if execution.plan_json:
        plan_summary = parse_plan(execution.plan_json)

    return render_template(
        "execution_detail.html",
        execution=execution,
        plan_summary=plan_summary,
    )


@execution_bp.route("/<execution_id>/cancel", methods=["POST"])
def cancel_execution(execution_id: str):
    eq = current_app.config["EXECUTION_QUEUE"]
    ok = eq.cancel(execution_id)
    return jsonify({"ok": ok})


@execution_bp.route("/<execution_id>/logs")
def execution_logs(execution_id: str):
    """Return current logs as plain text (polling fallback)."""
    eq = current_app.config["EXECUTION_QUEUE"]
    execution = eq.get(execution_id)
    if execution is None:
        return "Execution not found", 404
    return "\n".join(execution.logs), 200, {"Content-Type": "text/plain"}
