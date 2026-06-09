from flask import Blueprint, render_template

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin/fact-checker")
def fact_checker():
    return render_template("fact_checker.html")


@admin_bp.route("/admin/agent-map")
def agent_map():
    return render_template("agent_map.html")
