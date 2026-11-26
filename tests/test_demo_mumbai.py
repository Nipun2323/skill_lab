"""The Mumbai demo people: data checks and prediction checks with the real algorithm."""
import time
from datetime import datetime, timedelta, timezone

from app import create_app
from app.services import demo
from scripts import demo_report
from tests.conftest import write_venues

MUMBAI = (18.88, 19.30, 72.77, 73.00)  # south, north, west, east
SIX_MONTHS = 183 * 86400


def test_places_are_in_mumbai():
    for key, (name, cat, lat, lon, _) in demo.PLACES.items():
        assert MUMBAI[0] <= lat <= MUMBAI[1] and MUMBAI[2] <= lon <= MUMBAI[3], key
    assert sum(p[4] for p in demo.PLACES.values()) == 9  # coordinates checked on OpenStreetMap


def test_three_people_six_months_varied_places():
    now = int(time.time())
    assert len(demo.PEOPLE) == 3
    for person in demo.PEOPLE:
        rows = demo.history(person, now)
        assert len({r[0] for r in rows}) >= 10
        assert now - 7 * 86400 < rows[-1][1] <= now
        assert rows[0][1] >= now - SIX_MONTHS - 7 * 86400
        assert all(r[2] == demo.IST for r in rows)
        assert [r[1] for r in rows] == sorted(r[1] for r in rows)
        assert all(a[0] != b[0] for a, b in zip(rows, rows[1:]))
    assert demo.template_history("priya") == demo.template_history("priya")  # deterministic


def test_scenarios_match_the_routines():
    from config import load_settings
    from predictor.types import AlgoConfig
    results = demo_report.run_scenarios(AlgoConfig.from_settings(load_settings()))
    checks = [s for s in results if s["kind"] == "check"]
    failed = [(s["person"], s["at"], s["when"], s["top3"]) for s in checks if not s["passed"]]
    assert not failed
    assert sum(s["rank_of_expected"] == 1 for s in checks) >= len(checks) // 2


def test_full_rule_beats_baselines_on_each_person():
    from config import load_settings
    from predictor.types import AlgoConfig
    res = demo_report.run_replay(AlgoConfig.from_settings(load_settings()))
    for person, r in res.items():
        assert r["full"]["top3"] > r["most_visited"]["top3"], person
        assert r["full"]["top3"] >= r["markov"]["top3"], person


def _app(tmp_path):
    venues = tmp_path / "v.csv.gz"
    write_venues(venues)
    return create_app({"DATABASE_PATH": tmp_path / "d.db", "VENUES_PATH": venues,
                       "SAMPLES_PATH": tmp_path / "none.json.gz", "SECRET_KEY": "t", "DEMO_DATA": True})


def test_demo_people_seeded_and_predictable_through_the_app(tmp_path):
    app = _app(tmp_path)
    with app.app_context():
        from app.db import get_db
        from app.services import prediction_service as ps
        db = get_db()
        ids = app.config["DEMO_USERS"]
        assert set(ids) == {"aarav", "priya", "rohan"}
        for uid in ids.values():
            last = db.execute("SELECT MAX(ts_utc) FROM checkins WHERE user_id = ?", (uid,)).fetchone()[0]
            assert last > time.time() - 8 * 86400
        # what-if: Priya at Dadar Station on a weekday evening -> home first
        now = datetime.now(timezone.utc) + timedelta(minutes=demo.IST)
        days = (1 - now.weekday()) % 7 or 7  # next Tuesday, local
        local = (now + timedelta(days=days)).replace(hour=18, minute=55, second=0, microsecond=0)
        ts = int(local.replace(tzinfo=timezone.utc).timestamp()) - demo.IST * 60
        out = ps.predict_for_user(ids["priya"], ts, demo.IST, 3, at_venue=demo.venue_id("dadar_stn"))
        assert out["last_place"]["name"] == "Dadar Station"
        assert out["predictions"][0]["name"] == "Home · Dadar West"
        assert all(p["name"] for p in out["predictions"])
        n_pred = db.execute("SELECT COUNT(*) FROM predictions WHERE user_id = ?", (ids["priya"],)).fetchone()[0]
        assert n_pred == 0  # what-if predictions are not logged
    # restarting keeps the same people and does not duplicate check-ins
    count = lambda a: a.test_client() and __import__("sqlite3").connect(tmp_path / "d.db").execute(
        "SELECT COUNT(*) FROM checkins").fetchone()[0]
    before = count(app)
    app2 = _app(tmp_path)
    assert app2.config["DEMO_USERS"] == app.config["DEMO_USERS"] and count(app2) == before


def test_demo_accounts_cannot_log_in_with_a_password(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    import re
    tok = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/login").get_data(as_text=True)).group(1)
    r = client.post("/login", data={"csrf_token": tok, "email": "priya@demo.syncromove",
                                    "password": "demo-account:no-password-login"})
    assert r.status_code == 401
