"""Glue between the database and the algorithm: history -> scores -> top k with details."""
from __future__ import annotations

from flask import current_app

from config import ALGORITHM_VERSION
from predictor.algorithm import (
    bucket_label, build_profile, candidates_for, haversine_km, reason_text, score_candidates, top_k,
    time_bucket,
)
from predictor.types import AlgoConfig, CheckIn

from . import repository as repo

COLD_START_REASON = "Based on what's popular until you check in"


def venue_cache() -> dict:
    """{venue_id: (category, lat, lon, popularity, name, owner_user_id)} kept in memory; 38k rows."""
    cache = current_app.extensions.get("syncromove_venues")
    if cache is None:
        cache = repo.all_venues()
        current_app.extensions["syncromove_venues"] = cache
        # city popularity comes from the dataset only; users' own places have none
        current_app.extensions["syncromove_popularity"] = {
            v: t[3] for v, t in cache.items() if t[5] is None}
    return cache


def remember_venue(venue_id: str, category: str, lat: float, lon: float, name, owner: int) -> None:
    venue_cache()[venue_id] = (category, lat, lon, 0, name, owner)


def popularity() -> dict:
    venue_cache()
    return current_app.extensions["syncromove_popularity"]


def venue_dict(venue_id: str, user_id: int | None = None) -> dict | None:
    """Venue details. With user_id, another user's private place counts as not found."""
    t = venue_cache().get(venue_id)
    if t is None or (user_id is not None and t[5] is not None and t[5] != user_id):
        return None
    return {"venue_id": venue_id, "category": t[0], "lat": t[1], "lon": t[2], "popularity": t[3],
            "name": t[4], "own_place": t[5] is not None}


def algo_config() -> AlgoConfig:
    return AlgoConfig.from_settings(current_app.config)


def predict_for_user(user_id: int, query_ts: int, query_tz: int, k: int, log: bool = True) -> dict:
    cfg = algo_config()
    rows = repo.history(user_id, cfg.history_length)
    profile = build_profile([CheckIn(r["venue_id"], r["ts_utc"], r["tz_offset_min"]) for r in rows], cfg)
    pop = popularity()
    cold_start = not candidates_for(profile)
    scores = score_candidates(profile, query_ts, query_tz, pop, cfg)
    preds = top_k(scores, k, pop)

    last_place = venue_dict(profile.current) if profile.current else None
    current_category = last_place["category"] if last_place else None
    out = []
    for rank, p in enumerate(preds, start=1):
        v = venue_dict(p.venue_id)
        dist = (round(haversine_km(last_place["lat"], last_place["lon"], v["lat"], v["lon"]), 2)
                if last_place else None)
        reason = COLD_START_REASON if cold_start else reason_text(p.reason, current_category)
        out.append({
            "rank": rank,
            "venue_id": p.venue_id,
            "category": v["category"],
            "name": v["name"],
            "lat": v["lat"],
            "lon": v["lon"],
            "probability": round(p.probability, 4),
            "score": round(p.score, 6),
            "distance_km": dist,
            "reason_code": p.reason,
            "reason": reason,
            "signals": {name: round(p.signals[name], 4)
                        for name in ("transition", "time", "frequency", "recency", "popularity")},
        })

    context_id = rows[-1]["checkin_id"] if rows else None
    if log and out:
        repo.log_predictions(user_id, context_id, out, ALGORITHM_VERSION)

    if last_place is not None:
        last_place = {k_: last_place[k_] for k_ in ("venue_id", "category", "name", "lat", "lon")}
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "cold_start": cold_start,
        "history_used": len(profile),
        "query_ts_utc": query_ts,
        "time_bucket": bucket_label(time_bucket(query_ts, query_tz, cfg.time_bucket_hours),
                                    cfg.time_bucket_hours),
        "weights": cfg.weights,
        "last_place": last_place,
        "predictions": out,
    }
