"""SyncroMove Flask application factory."""
from __future__ import annotations

import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from config import load_settings

from . import db as dbmod
from .security import check_csrf, csrf_token, current_user
from .validators import ValidationError

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; object-src 'none'")


def _secret_key(settings: dict) -> str:
    if settings.get("SECRET_KEY"):
        return str(settings["SECRET_KEY"])
    key_file = Path(settings["DATABASE_PATH"]).parent / "secret_key"
    if key_file.is_file():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    try:
        key_file.chmod(0o600)
    except OSError:
        pass
    return key


def create_app(test_config: dict | None = None) -> Flask:
    settings = load_settings(test_config)
    app = Flask(__name__, instance_relative_config=False)
    Path(settings["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    app.config.update(settings)
    app.config.update(
        SECRET_KEY=_secret_key(settings),
        MAX_CONTENT_LENGTH=32 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="syncromove_session",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=int(settings["SESSION_HOURS"])),
    )
    if test_config:
        app.config.update({k: v for k, v in test_config.items() if k not in settings})

    # Schema + venue sync on start
    conn = dbmod.connect(app.config["DATABASE_PATH"])
    try:
        dbmod.init_db(conn)
        app.config["VENUES_STATUS"] = dbmod.load_venues(conn, app.config["VENUES_PATH"])
    finally:
        conn.close()

    app.json.sort_keys = False
    app.teardown_appcontext(dbmod.close_db)

    from . import api, auth, views
    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    app.register_blueprint(api.bp)

    @app.before_request
    def _csrf():
        check_csrf()

    @app.after_request
    def _headers(resp):
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if not request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def _inject():
        return {"csrf_token": csrf_token, "current_user": current_user()}

    @app.errorhandler(ValidationError)
    def _validation(err: ValidationError):
        return jsonify(err.as_dict()), err.status

    @app.errorhandler(HTTPException)
    def _http(err: HTTPException):
        if err.response is not None and err.response.mimetype == "application/json":
            return err.response
        messages = {
            404: "We couldn't find that page.",
            405: "That action isn't allowed here.",
            413: "The request was too large (limit 32 KB).",
        }
        message = messages.get(err.code) or err.description or err.name
        if request.path.startswith("/api/"):
            code = {404: "not_found", 405: "method_not_allowed", 413: "too_large",
                    403: "forbidden", 400: "bad_request"}.get(err.code, "error")
            return jsonify({"error": {"code": code, "field": None, "message": message}}), err.code
        return render_template("error.html", code=err.code, message=message), err.code

    @app.errorhandler(Exception)
    def _unexpected(err: Exception):
        app.logger.exception("Unhandled error")
        if request.path.startswith("/api/"):
            return jsonify({"error": {"code": "server_error", "field": None,
                                      "message": "Something went wrong on our side."}}), 500
        return render_template("error.html", code=500, message="Something went wrong on our side."), 500

    return app
