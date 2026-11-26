import gzip
import json
import time
from datetime import datetime, timezone

from app.services import places
from app.services.samples import WEEK, shift_to_recent
from scripts import generate_synthetic
from tests.conftest import ROOT, V

YEAR = 365 * 86400


def test_shift_keeps_weekday_and_time_and_lands_in_past_week():
    now = int(time.time())
    rows = [["a", 1_350_000_000, -240], ["b", 1_350_090_000, -240], ["c", 1_351_000_000, -240]]
    out = shift_to_recent(rows, now)
    shift = out[0][1] - rows[0][1]
    assert shift % WEEK == 0
    assert all(o[1] - r[1] == shift and o[0] == r[0] and o[2] == r[2] for o, r in zip(out, rows))
    assert now - WEEK < out[-1][1] <= now
    # a history that ends after `now` moves back
    future = [["a", now + 3 * 86400, 0]]
    assert now - WEEK < shift_to_recent(future, now)[0][1] <= now


def test_committed_synthetic_histories_meet_the_brief():
    data = json.loads(gzip.decompress((ROOT / "data/samples/synthetic_histories.json.gz").read_bytes()))
    now = int(time.time())
    assert len(data) == 25 and all(k.startswith("synthetic_") for k in data)
    for rows in data.values():
        assert len({r[0] for r in rows}) >= 10
        assert [r[1] for r in rows] == sorted(r[1] for r in rows)
        assert all(a[0] != b[0] for a, b in zip(rows, rows[1:]))  # no back-to-back repeats
        moved = shift_to_recent(rows, now)
        assert moved[0][1] >= now - YEAR and moved[-1][1] <= now


def test_generator_is_deterministic_and_uses_real_venues(tmp_path):
    out1, out2 = tmp_path / "a.json.gz", tmp_path / "b.json.gz"
    for out in (out1, out2):
        assert generate_synthetic.main(["--people", "3", "--end", "2026-06-30", "--out", str(out)]) == 0
    assert out1.read_bytes() == out2.read_bytes()
    data = json.loads(gzip.decompress(out1.read_bytes()))
    venues = generate_synthetic.load_venues(ROOT / "data/venues.csv.gz")
    ids = {v[0] for vs in venues.values() for v in vs}
    assert all(r[0] in ids for rows in data.values() for r in rows)
    end = datetime(2026, 7, 1, 12, tzinfo=timezone.utc).timestamp()
    assert all(rows[-1][1] <= end and rows[0][1] >= end - YEAR for rows in data.values())


def test_signup_sample_is_in_the_past_year(session, client):
    session.register(sample=True)
    rows = client.get("/api/checkins?limit=100").get_json()["checkins"]
    now = time.time()
    assert rows and all(now - YEAR <= c["ts_utc"] <= now for c in rows)


def test_parse_nominatim():
    payload = {"osm_type": "node", "osm_id": 42, "lat": "12.97", "lon": "77.59", "category": "amenity",
               "type": "cafe", "name": "Brew Lab", "display_name": "Brew Lab, MG Road, Bengaluru"}
    assert places.parse_nominatim(payload) == {"osm_key": "node:42", "name": "Brew Lab", "category": "Café",
                                               "lat": 12.97, "lon": 77.59}
    house = {"osm_type": "way", "osm_id": 7, "lat": "1", "lon": "2", "category": "building", "type": "house",
             "name": "", "address": {"house_number": "12", "road": "Elm St"}}
    assert places.parse_nominatim(house)["name"] == "12 Elm St"
    assert places.parse_nominatim(house)["category"] == "Home (private)"
    assert places.parse_nominatim({"error": "Unable to geocode"}) is None


def test_locate_creates_private_place(session, client, monkeypatch):
    found = {"osm_key": "node:1", "name": "Brew Lab", "category": "Café", "lat": 12.97, "lon": 77.59}
    monkeypatch.setattr(places, "reverse_geocode", lambda lat, lon: found)
    session.register("loc@example.com")
    assert session.post_json("/api/places/locate", {"lat": 95, "lon": 0}).status_code == 400
    r = session.post_json("/api/places/locate", {"lat": 12.9701, "lon": 77.5901})
    body = r.get_json()
    assert r.status_code == 200 and body["lookup"] == "ok" and body["detected"]["name"] == "Brew Lab"
    vid = body["detected"]["venue_id"]
    r = session.post_json("/api/checkins", {"venue_id": vid, "timestamp": None, "tz_offset_min": 330})
    assert r.status_code == 201 and r.get_json()["name"] == "Brew Lab"
    session.logout()
    session.register("other@example.com")  # another user can't see or use it
    assert client.get("/api/venues?q=brew").get_json()["venues"] == []
    r = session.post_json("/api/checkins", {"venue_id": vid, "timestamp": None, "tz_offset_min": 330})
    assert r.status_code == 404


def test_locate_falls_back_to_a_pin(session, monkeypatch):
    monkeypatch.setattr(places, "reverse_geocode", lambda lat, lon: None)
    session.register("pin@example.com")
    body = session.post_json("/api/places/locate", {"lat": 40.7005, "lon": -74.0005}).get_json()
    assert body["lookup"] == "failed" and body["detected"]["category"] == places.PIN_CATEGORY
    assert any(v["venue_id"] == V["home"] for v in body["nearby"])  # the test city's home venue, about 70 m away
