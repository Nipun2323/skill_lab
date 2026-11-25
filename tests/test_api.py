from datetime import datetime, timedelta, timezone

from tests.conftest import V


def iso(dt):
    return dt.isoformat()


def test_predictions_require_login(client):
    assert client.get("/api/predictions").status_code == 401
    assert client.get("/api/checkins").status_code == 401


def test_health(client):
    body = client.get("/api/health").get_json()
    assert body["status"] == "ok" and body["venues_loaded"] == len(V)
    assert body["algorithm_version"] == "rule-v1"


def test_health_degraded_without_venue_file(tmp_path):
    from app import create_app
    app = create_app({"DATABASE_PATH": tmp_path / "x.db", "VENUES_PATH": tmp_path / "missing.csv.gz",
                      "SAMPLES_PATH": tmp_path / "none.json.gz", "SECRET_KEY": "t"})
    body = app.test_client().get("/api/health").get_json()
    assert body["status"] == "degraded" and "import_dataset.py" in body["message"]


def test_k_bounds_and_tz(session, client):
    session.register(sample=True)
    assert client.get("/api/predictions?k=0").status_code == 400
    assert client.get("/api/predictions?k=11").status_code == 400
    assert client.get("/api/predictions?k=abc").status_code == 400
    assert client.get("/api/predictions?tz=900").status_code == 400
    body = client.get("/api/predictions?k=10&tz=-240").get_json()
    assert len(body["predictions"]) <= 10
    body = client.get("/api/predictions?k=3&tz=330").get_json()
    assert [p["rank"] for p in body["predictions"]] == [1, 2, 3]
    assert body["last_place"]["venue_id"] == V["office"]
    assert all(p["venue_id"] != V["office"] for p in body["predictions"])
    assert set(body["predictions"][0]["signals"]) == {"transition", "time", "frequency", "recency", "popularity"}
    assert client.get("/api/predictions?at=2024-05-01T19:00:00").status_code == 400  # no offset
    assert client.get("/api/predictions?at=2024-05-01T19:00:00%2B05:30").status_code == 200


def test_cold_start(session, client):
    session.register()
    body = client.get("/api/predictions").get_json()
    assert body["cold_start"] is True
    assert body["predictions"][0]["venue_id"] == V["museum"]  # most popular in the test city
    assert body["predictions"][0]["reason"].startswith("Based on what's popular")


def test_checkin_validation(session, client):
    session.register(sample=True)
    now = datetime.now(timezone.utc)
    r = session.post_json("/api/checkins", {"venue_id": V["bar"], "timestamp": iso(now + timedelta(hours=1)),
                                            "tz_offset_min": 0})
    assert r.status_code == 400 and r.get_json()["error"]["field"] == "timestamp"
    r = session.post_json("/api/checkins", {"venue_id": V["office"], "timestamp": None, "tz_offset_min": 0})
    assert r.status_code == 409 and r.get_json()["error"]["message"] == "You're already checked in here."
    r = session.post_json("/api/checkins", {"venue_id": V["bar"], "timestamp": "2010-01-01T00:00:00+00:00",
                                            "tz_offset_min": 0})
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "out_of_order"
    r = session.post_json("/api/checkins", {"venue_id": "f" * 24, "timestamp": None, "tz_offset_min": 0})
    assert r.status_code == 404
    r = session.post_json("/api/checkins", {"venue_id": "nope", "tz_offset_min": 0})
    assert r.status_code == 400
    r = session.post_json("/api/checkins", {"venue_id": V["bar"], "tz_offset_min": 2000})
    assert r.status_code == 400
    r = client.post("/api/checkins", data="[1,2]", content_type="application/json",
                    headers={"X-CSRF-Token": session.token})
    assert r.status_code == 400 and "JSON object" in r.get_json()["error"]["message"]
    r = client.post("/api/checkins", data="x" * 40000, content_type="application/json",
                    headers={"X-CSRF-Token": session.token})
    assert r.status_code == 413


def test_predicted_rank_recorded(session, client):
    session.register(sample=True)
    # no prediction shown yet for this place -> null
    r = session.post_json("/api/checkins", {"venue_id": V["bar"], "timestamp": None, "tz_offset_min": 0})
    assert r.status_code == 201 and r.get_json()["predicted_rank"] is None
    preds = client.get("/api/predictions?k=3").get_json()["predictions"]
    second = preds[1]["venue_id"]
    r = session.post_json("/api/checkins", {"venue_id": second, "timestamp": None, "tz_offset_min": 0})
    assert r.get_json()["predicted_rank"] == 2
    shown = {p["venue_id"] for p in client.get("/api/predictions?k=3").get_json()["predictions"]}
    miss = next(v for v in V.values() if v not in shown and v != second)
    r = session.post_json("/api/checkins", {"venue_id": miss, "timestamp": None, "tz_offset_min": 0})
    assert r.get_json()["predicted_rank"] == 0
    stats = client.get("/api/checkins").get_json()["stats"]
    assert stats == {"total": 12, "own": 3, "scored": 2, "hits_top3": 1}


def test_prediction_log_skips_identical_batches(session, client, app):
    session.register(sample=True)
    client.get("/api/predictions")
    client.get("/api/predictions")
    export = client.get("/account/export").get_json()
    assert len({p["batch_id"] for p in export["predictions"]}) == 1


def test_users_are_isolated(session, client, app):
    session.register("a@example.com", sample=True)
    a_ids = {c["checkin_id"] for c in client.get("/api/checkins?limit=100").get_json()["checkins"]}
    session.logout()
    session.register("b@example.com")
    b = client.get("/api/checkins?limit=100").get_json()
    assert b["checkins"] == [] and b["stats"]["total"] == 0
    assert a_ids  # user A really has data
    assert client.get("/account/export").get_json()["checkins"] == []


def test_venue_search(session, client):
    session.register(sample=True)
    body = client.get("/api/venues?q=coff").get_json()
    assert body["near_last_checkin"] is True
    assert [v["category"] for v in body["venues"]] == ["Coffee Shop"]
    assert client.get("/api/venues?q=" + "x" * 61).status_code == 400
    assert client.get("/api/venues?q=").status_code == 400
    assert client.get("/api/venues?q=%25").get_json()["venues"] == []  # LIKE wildcard escaped


def test_delete_account_removes_everything(session, client, app):
    session.register("del@example.com", sample=True)
    client.get("/api/predictions")
    uid = None
    with app.app_context():
        from app.db import get_db
        uid = get_db().execute("SELECT user_id FROM users WHERE email = 'del@example.com'").fetchone()[0]
    r = client.post("/account", data={"csrf_token": session.token, "action": "delete_account",
                                      "password": "wrong1234"})
    assert r.status_code == 401
    r = client.post("/account", data={"csrf_token": session.token, "action": "delete_account",
                                      "password": "secret123"})
    assert r.status_code == 302
    with app.app_context():
        from app.db import get_db
        db = get_db()
        for table in ("users", "checkins", "predictions"):
            assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (uid,)).fetchone()[0] == 0
    assert client.get("/dashboard").status_code == 302


def test_export_and_delete_checkins(session, client):
    session.register("x@example.com", sample=True)
    data = client.get("/account/export")
    assert data.headers["Content-Disposition"].startswith("attachment")
    assert len(data.get_json()["checkins"]) == 9
    client.post("/account", data={"csrf_token": session.token, "action": "delete_checkins",
                                  "password": "secret123"})
    assert client.get("/api/checkins").get_json()["stats"]["total"] == 0
