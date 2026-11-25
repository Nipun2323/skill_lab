"""Server-rendered pages: dashboard and account."""
from __future__ import annotations

import json

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from .security import current_user, login_required, logout_user
from .services import repository as repo

bp = Blueprint("views", __name__)


@bp.route("/")
def index():
    return redirect(url_for("views.dashboard" if current_user() else "auth.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    user = current_user()
    stats = repo.checkin_stats(user["user_id"])
    if request.method == "GET":
        return render_template("account.html", stats=stats, errors={})

    action = request.form.get("action")
    pw = request.form.get("password") or ""
    errors = {}
    if action not in ("delete_checkins", "delete_account"):
        errors["form"] = "Choose what to delete."
        return render_template("account.html", stats=stats, errors=errors), 400
    if not pw:
        errors[action] = "Enter your password to confirm."
        return render_template("account.html", stats=stats, errors=errors), 400
    stored = repo.get_password_hash(user["user_id"])
    if not stored or not check_password_hash(stored, pw):
        errors[action] = "That password isn't right."
        return render_template("account.html", stats=stats, errors=errors), 401

    if action == "delete_checkins":
        repo.delete_checkins(user["user_id"])
        flash("All your check-ins and predictions were deleted.")
        return redirect(url_for("views.account"))
    repo.delete_user(user["user_id"])
    logout_user()
    flash("Your account and all its data were deleted.")
    return redirect(url_for("auth.register"))


@bp.route("/account/export")
@login_required
def export():
    data = repo.export_user(current_user()["user_id"])
    body = json.dumps(data, indent=2)
    return Response(body, mimetype="application/json",
                    headers={"Content-Disposition": 'attachment; filename="syncromove-export.json"'})
