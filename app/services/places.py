"""Places near the user's detected location.

The browser supplies coordinates (Geolocation API, with the user's permission). The server then
1. lists dataset venues near those coordinates, and
2. asks OpenStreetMap's Nominatim service for the address at those coordinates and saves it as a
   place visible only to this user, so check-ins work anywhere, not just in New York.

Nominatim's usage policy: at most 1 request per second, an identifying User-Agent, and results
cached where possible. All three are done here. The coordinates go to the OpenStreetMap
Foundation's server; the UI says so before the user shares their location.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request

from predictor.algorithm import haversine_km

from . import prediction_service as ps
from . import repository as repo

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "SyncroMove/1.0 (student next-place prediction demo)"
NEARBY_KM = 0.5

# OSM tag value -> category label in the dataset's style
CATEGORY_LABELS = {
    "cafe": "Café", "coffee_shop": "Coffee Shop", "restaurant": "Restaurant", "fast_food": "Fast Food Restaurant",
    "food_court": "Food Court", "bar": "Bar", "pub": "Pub", "nightclub": "Nightclub", "ice_cream": "Ice Cream Shop",
    "bakery": "Bakery", "fitness_centre": "Gym / Fitness Center", "gym": "Gym / Fitness Center",
    "sports_centre": "Athletic & Sport", "stadium": "Stadium", "swimming_pool": "Pool",
    "university": "University", "college": "College Academic Building", "school": "School", "library": "Library",
    "hospital": "Hospital", "clinic": "Medical Center", "doctors": "Doctor's Office", "dentist": "Dentist's Office",
    "pharmacy": "Drugstore / Pharmacy", "supermarket": "Grocery Store", "convenience": "Convenience Store",
    "mall": "Mall", "department_store": "Department Store", "clothes": "Clothing Store",
    "electronics": "Electronics Store", "bank": "Bank", "atm": "Bank", "post_office": "Post Office",
    "place_of_worship": "Place of Worship", "cinema": "Movie Theater", "theatre": "Theater", "museum": "Museum",
    "park": "Park", "playground": "Playground", "garden": "Garden", "hotel": "Hotel", "office": "Office",
    "commercial": "Office", "company": "Office", "coworking_space": "Office",
    "house": "Home (private)", "residential": "Residential Building (Apartment / Condo)",
    "apartments": "Residential Building (Apartment / Condo)", "detached": "Home (private)",
    "station": "Train Station", "train_station": "Train Station", "subway_entrance": "Subway",
    "bus_station": "Bus Station", "bus_stop": "Bus Station", "airport": "Airport", "aerodrome": "Airport",
    "fuel": "Gas Station / Garage", "parking": "Parking",
}
PIN_CATEGORY = "Pinned location"

_lock = threading.Lock()
_last_call = 0.0
_cache: dict = {}


def category_label(osm_class: str | None, osm_type: str | None) -> str:
    for key in (osm_type, osm_class):
        if key and key in CATEGORY_LABELS:
            return CATEGORY_LABELS[key]
    if osm_class == "highway":
        return "Street"
    if osm_type and osm_type not in ("yes", "unclassified"):
        return osm_type.replace("_", " ").capitalize()
    return PIN_CATEGORY


def parse_nominatim(payload: dict) -> dict | None:
    """Nominatim jsonv2 reverse result -> {osm_key, name, category, lat, lon}, or None."""
    if not isinstance(payload, dict) or "error" in payload or "lat" not in payload:
        return None
    address = payload.get("address") or {}
    name = (payload.get("name") or "").strip()
    if not name:
        road = address.get("road") or address.get("pedestrian") or ""
        number = address.get("house_number") or ""
        name = f"{number} {road}".strip() or (payload.get("display_name") or "").split(",")[0].strip()
    osm_key = f"{payload.get('osm_type', '')}:{payload.get('osm_id', '')}"
    return {
        "osm_key": osm_key,
        "name": name[:120] or None,
        "category": category_label(payload.get("category"), payload.get("type")),
        "lat": float(payload["lat"]),
        "lon": float(payload["lon"]),
    }


def reverse_geocode(lat: float, lon: float, timeout: float = 6.0) -> dict | None:
    """Ask Nominatim what is at (lat, lon). None if the lookup fails. Throttled and cached."""
    global _last_call
    key = (round(lat, 4), round(lon, 4))  # ~11 m
    if key in _cache:
        return _cache[key]
    query = urllib.parse.urlencode({"format": "jsonv2", "lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
                                    "zoom": 18, "addressdetails": 1})
    req = urllib.request.Request(f"{NOMINATIM_URL}?{query}",
                                 headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with _lock:
        wait = 1.0 - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = parse_nominatim(json.loads(resp.read().decode("utf-8")))
        except Exception:
            return None
    if len(_cache) > 512:
        _cache.clear()
    _cache[key] = result
    return result


def own_venue_id(user_id: int, key: str) -> str:
    """24-hex id, unique per user, so one user's places never show up for another."""
    return hashlib.sha1(f"user{user_id}:{key}".encode()).hexdigest()[:24]


def nearby_venues(user_id: int, lat: float, lon: float, limit: int = 8, radius_km: float = NEARBY_KM) -> list:
    out = []
    dlat = radius_km / 111.0
    for vid, t in ps.venue_cache().items():
        if abs(t[1] - lat) > dlat or (t[5] is not None and t[5] != user_id):
            continue
        d = haversine_km(lat, lon, t[1], t[2])
        if d <= radius_km:
            out.append({"venue_id": vid, "category": t[0], "name": t[4], "lat": t[1], "lon": t[2],
                        "popularity": t[3], "distance_km": round(d, 3), "own_place": t[5] is not None})
    out.sort(key=lambda x: (x["distance_km"], -x["popularity"], x["venue_id"]))
    return out[:limit]


def locate(user_id: int, lat: float, lon: float, lookup=None) -> dict:
    """Save the place at the user's position and list places near it.
    Returns {"detected": venue, "lookup": "ok" | "failed", "nearby": [venues]}."""
    found = (lookup or reverse_geocode)(lat, lon)
    if found:
        vid = own_venue_id(user_id, found["osm_key"])
        category, name, vlat, vlon = found["category"], found["name"], found["lat"], found["lon"]
    else:
        vid = own_venue_id(user_id, f"pin:{lat:.4f},{lon:.4f}")
        category, name, vlat, vlon = PIN_CATEGORY, None, round(lat, 6), round(lon, 6)
    repo.upsert_own_venue(user_id, vid, category, vlat, vlon, name)
    ps.remember_venue(vid, category, vlat, vlon, name, user_id)
    detected = {"venue_id": vid, "category": category, "name": name, "lat": vlat, "lon": vlon,
                "distance_km": round(haversine_km(lat, lon, vlat, vlon), 3), "own_place": True}
    nearby = [v for v in nearby_venues(user_id, lat, lon) if v["venue_id"] != vid]
    return {"detected": detected, "lookup": "ok" if found else "failed", "nearby": nearby}
