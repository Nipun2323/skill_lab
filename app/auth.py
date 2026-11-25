"""Registration, login and logout (server-rendered forms)."""
from __future__ import annotations

import gzip
import json
import math
import secrets
import sqlite3
import time
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import validators as v
from .security import client_ip, current_user, login_user, logout_user, safe_next
from .services import repository as repo

bp = Blueprint("auth", __name__)

# Compared against when the email is unknown, so both paths cost the same.
_DUMMY_HASH = generate_password_hash("not-a-real-password-1")


def sample_histories() -> dict:
    cache = current_app.extensions.get("syncromove_samples")
    if cache is None:
        path = Path(current_app.config["SAMPLES_PATH"])
        cache = json.loads(gzip.decompress(path.read_bytes())) if path.is_file() else {}
        current_app.extensions["syncromove_samples"] = cache
    return cache


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user() is not None:
        return redirect(url_for("views.dashboard"))
    samples_available = bool(sample_histories())
    if request.method == "GET":
        return render_template("register.html", errors={}, form={"sample": "1"},
                               samples_available=samples_available)

    form = request.form
    errors: dict[str, str] = {}
    email = form.get("email", "")
    try:
        email = v.email(email)
    except v.ValidationError as e:
        errors[e.field] = e.message
    try:
        v.password(form.get("password", ""), form.get("confirm", ""),
                   minimum=int(current_app.config["PASSWORD_MIN"]))
    except v.ValidationError as e:
        errors[e.field] = e.message
    status = 400
    if not errors and repo.email_exists(email):
        errors["email"] = "An account with this email already exists. Log in instead."
        status = 409
    if errors:
        return render_template("register.html", errors=errors, form=form,
                               samples_available=samples_available), status

    sample_key, sample_rows = None, None
    if form.get("sample") == "1" and samples_available:
        samples = sample_histories()
        sample_key = secrets.choice(sorted(samples))
        sample_rows = samples[sample_key]
    try:
        user_id = repo.create_user(email, generate_password_hash(form["password"]), sample_key)
    except sqlite3.IntegrityError:
        errors["email"] = "An account with this email already exists. Log in instead."
        return render_template("register.html", errors=errors, form=form,
                               samples_available=samples_available), 409
    if sample_rows:
        repo.copy_sample(user_id, sample_rows)
    login_user(user_id)
    flash("Welcome aboard. " + ("We loaded a sample New York history so you can see predictions now."
                                if sample_rows else "Your line starts with your first check-in."))
    return redirect(url_for("views.dashboard"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = safe_next(request.values.get("next"))
    if current_user() is not None:
        return redirect(next_url or url_for("views.dashboard"))
    if request.method == "GET":
        return render_template("login.html", errors={}, form={}, next=next_url)

    form = request.form
    email = (form.get("email") or "").strip()
    pw = form.get("password") or ""
    errors = {}
    if not email:
        errors["email"] = "Enter your email address."
    elif len(email) > 254:
        errors["email"] = "Email must be 254 characters or fewer."
    if not pw:
        errors["password"] = "Enter your password."
    if errors:
        return render_template("login.html", errors=errors, form=form, next=next_url), 400

    cfg = current_app.config
    window = int(cfg["LOGIN_WINDOW_MINUTES"]) * 60
    now = int(time.time())
    ip = client_ip()
    by_email, by_ip, oldest = repo.recent_failures(email, ip, now - window)
    if max(by_email, by_ip) >= int(cfg["LOGIN_MAX_ATTEMPTS"]):
        wait_min = max(1, math.ceil(((oldest or now) + window - now) / 60))
        errors["form"] = (f"Too many failed attempts. Try again in {wait_min} "
                          f"minute{'s' if wait_min != 1 else ''}.")
        return render_template("login.html", errors=errors, form=form, next=next_url), 429

    row = repo.get_auth_row(email)
    ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, pw) and row is not None
    if not ok:
        repo.record_failure(email, ip, now)
        errors["form"] = "Email or password is incorrect."
        return render_template("login.html", errors=errors, form=form, next=next_url), 401

    repo.clear_failures(email, now - window)
    login_user(row["user_id"])
    return redirect(next_url or url_for("views.dashboard"))


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You're logged out.")
    return redirect(url_for("auth.login"))
