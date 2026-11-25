def test_register_login_logout(session, client):
    r = session.register("Alice@Example.com", "secret123")
    assert r.status_code == 302 and r.headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200
    assert session.logout().status_code == 302
    assert client.get("/dashboard").status_code == 302
    r = session.login("alice@example.com", "secret123")
    assert r.status_code == 302


def test_duplicate_email_any_case(session):
    assert session.register("bob@example.com").status_code == 302
    session.logout()
    r = session.register("BOB@example.COM")
    assert r.status_code == 409
    assert b"already exists" in r.data


def test_weak_password_rejected(session):
    r = session.register("c@example.com", "abcdefgh")
    assert r.status_code == 400 and b"at least one number" in r.data
    r = session.register("c@example.com", "1234567")
    assert r.status_code == 400 and b"at least 8 characters" in r.data
    r = session.register("not-an-email", "secret123")
    assert r.status_code == 400 and b"valid email" in r.data


def test_mismatched_confirm(session, client):
    tok = session.refresh_token("/register")
    r = client.post("/register", data={"csrf_token": tok, "email": "d@example.com",
                                       "password": "secret123", "confirm": "secret124"})
    assert r.status_code == 400 and b"don&#39;t match" in r.data


def test_wrong_password_401_and_rate_limit(session):
    session.register("e@example.com", "secret123")
    session.logout()
    for _ in range(5):
        assert session.login("e@example.com", "wrongpass1").status_code == 401
    r = session.login("e@example.com", "secret123")  # 6th attempt, even with the right password
    assert r.status_code == 429
    assert b"Try again in 15 minutes" in r.data


def test_post_without_csrf_rejected(client, session):
    r = client.post("/register", data={"email": "f@example.com", "password": "secret123", "confirm": "secret123"})
    assert r.status_code in (400, 403)
    session.register("g@example.com")
    r = client.post("/api/checkins", json={"venue_id": "0" * 24, "tz_offset_min": 0})
    assert r.status_code in (400, 403)
    assert r.get_json()["error"]["code"] == "csrf_failed"


def test_open_redirect_ignored(session):
    session.register("h@example.com")
    session.logout()
    for evil in ("//evil.com", "https://evil.com", "/\\evil.com"):
        r = session.login("h@example.com", "secret123", next=evil)
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/dashboard"), evil
        session.logout()
    r = session.login("h@example.com", "secret123", next="/account")
    assert r.headers["Location"].endswith("/account")


def test_session_rotated_on_login(session, client):
    session.register("i@example.com")
    old = session.token
    session.logout()
    session.login("i@example.com", "secret123")
    assert session.token != old


def test_security_headers(client):
    r = client.get("/login")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert client.get("/api/health").headers["Cache-Control"] == "no-store"
