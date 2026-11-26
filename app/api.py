"""JSON API. Session-cookie auth; POSTs need the X-CSRF-Token header (checked in before_request)."""
from __future__ import annotations

import json
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from config import ALGORITHM_VERSION
from predictor.algorithm import haversine_km

from . import validators as v
from .security import current_user, login_required
from .services import checkin_service
from .services import prediction_service as ps
from .services import repository as repo

bp = Blueprint("api", __name__, url_prefix="/api")


def _uid() -> int:
    return current_user()["user_id"]


@bp.get("/health")
def health():
    count = repo.venue_count()
    status = current_app.config.get("VENUES_STATUS")
    body = {"status": "ok", "venues_loaded": count, "algorithm_version": ALGORITHM_VERSION}
    if count == 0 or status == "missing":
        body["status"] = "degraded"
        body["message"] = (
            f"Venue file not found at {Path(current_app.config['VENUES_PATH']).name}"
            + ("; using venues already in the database." if count else "; no venues loaded.")
            + " Run: python scripts/import_dataset.py --data <NYC file>")
    return jsonify(body)


@bp.get("/predictions")
@login_required
def predictions():
    cfg = current_app.config
    k = v.k_value(request.args.get("k"), max_k=int(cfg["MAX_K"]), default=int(cfg["TOP_K"]))
    tz = v.tz_offset(request.args.get("tz", "0"), field="tz")
    at = request.args.get("at")
    query_ts = int(v.parse_iso(at, field="at").timestamp()) if at else int(time.time())
    return jsonify(ps.predict_for_user(_uid(), query_ts, tz, k))


def _checkin_json(r) -> dict:
    return {"checkin_id": r["checkin_id"], "venue_id": r["venue_id"], "category": r["category"],
            "lat": r["lat"], "lon": r["lon"], "ts_utc": r["ts_utc"],
            "tz_offset_min": r["tz_offset_min"], "source": r["source"],
            "predicted_rank": r["predicted_rank"]}


@bp.get("/checkins")
@login_required
def list_checkins():
    n = v.limit(request.args.get("limit"), 1, 100, 10)
    rows = repo.recent_checkins(_uid(), n)
    return jsonify({"checkins": [_checkin_json(r) for r in rows], "stats": repo.checkin_stats(_uid())})


@bp.post("/checkins")
@login_required
def create_checkin():
    body = v.json_object(request.get_json(silent=True))
    result = checkin_service.record_checkin(_uid(), body.get("venue_id"), body.get("timestamp"),
                                            body.get("tz_offset_min"))
    return jsonify(result), 201


@bp.get("/venues")
@login_required
def venues():
    q = v.search_query(request.args.get("q"))
    n = v.limit(request.args.get("limit"), 1, 50, 12)
    rows = repo.search_venues(q)
    last = repo.last_checkin(_uid())
    origin = ps.venue_dict(last["venue_id"]) if last else None
    items = []
    for r in rows:
        d = (haversine_km(origin["lat"], origin["lon"], r["latitude"], r["longitude"])
             if origin else None)
        items.append({"venue_id": r["venue_id"], "category": r["category"], "lat": r["latitude"],
                      "lon": r["longitude"], "popularity": r["popularity"],
                      "distance_km": round(d, 2) if d is not None else None})
    if origin:
        items.sort(key=lambda x: (x["distance_km"], -x["popularity"], x["venue_id"]))
    else:
        items.sort(key=lambda x: (-x["popularity"], x["venue_id"]))
    return jsonify({"venues": items[:n], "near_last_checkin": origin is not None})


@bp.get("/accuracy")
@login_required
def accuracy():
    path = Path(current_app.config["EVAL_RESULTS_PATH"])
    if not path.is_file():
        raise v.ValidationError(None, "No evaluation results yet. Run: python scripts/evaluate.py",
                                status=404, code="not_found")
    data = json.loads(path.read_text(encoding="utf-8"))
    return jsonify({
        "algorithm_version": data.get("algorithm_version"),
        "generated_at": data.get("generated_at"),
        "dataset": data.get("dataset"),
        "methods": data.get("methods"),
        "full_extras": data.get("full_extras"),
        "you": repo.checkin_stats(_uid()),
    })
