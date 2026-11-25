"""Sessions, CSRF protection, the current user and the login guard."""
from __future__ import annotations

import functools
import hmac
import secrets
from urllib.parse import urlsplit

from flask import abort, g, jsonify, redirect, request, session, url_for

from .services import repository as repo

CSRF_SESSION_KEY = "_csrf"


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def check_csrf() -> None:
    """Every POST must carry the session's token in a form field or the X-CSRF-Token header."""
    if request.method != "POST":
        return
    expected = session.get(CSRF_SESSION_KEY)
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not expected or not sent or not hmac.compare_digest(str(expected), str(sent)):
        if request.path.startswith("/api/"):
            resp = jsonify({"error": {"code": "csrf_failed", "field": None,
                                      "message": "Your session expired. Reload the page and try again."}})
            resp.status_code = 403
            abort(resp)
        abort(403, description="Your session expired or the form was out of date. Reload the page and try again.")


def login_user(user_id: int) -> None:
    """Fresh session on login: drop everything (including the old CSRF token)."""
    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["sid"] = secrets.token_urlsafe(16)
    csrf_token()


def logout_user() -> None:
    session.clear()


def current_user():
    if "user" in g:
        return g.user
    uid = session.get("user_id")
    g.user = repo.get_user(uid) if isinstance(uid, int) else None
    if uid is not None and g.user is None:
        session.clear()
    return g.user


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": {"code": "unauthorized", "field": None,
                                          "message": "Log in to continue."}}), 401
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def safe_next(target: str | None) -> str | None:
    """Only same-site relative paths: '/dashboard' yes; '//evil.com', 'http://..', '/\\evil' no."""
    if not target or not isinstance(target, str):
        return None
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    return target


def client_ip() -> str:
    return request.remote_addr or "unknown"
