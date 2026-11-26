"""Every SQL statement in the app. Functions that touch personal data take the session
user_id as their first argument; no endpoint passes a user id that came from the client."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from ..db import get_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ----------------------------------------------------------------------------- users

def create_user(email: str, password_hash: str, sample_source: str | None = None) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO users (email, password_hash, created_at, sample_source) VALUES (?, ?, ?, ?)",
        (email, password_hash, _now_iso(), sample_source))
    db.commit()
    return cur.lastrowid


def email_exists(email: str) -> bool:
    return get_db().execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone() is not None


def get_user(user_id: int):
    """Public user fields (no password hash)."""
    return get_db().execute(
        "SELECT user_id, email, created_at, sample_source FROM users WHERE user_id = ?",
        (user_id,)).fetchone()


def get_auth_row(email: str):
    """Only the login and password re-entry checks read password_hash."""
    return get_db().execute("SELECT user_id, password_hash FROM users WHERE email = ?",
                            (email,)).fetchone()


def get_password_hash(user_id: int) -> str | None:
    row = get_db().execute("SELECT password_hash FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row["password_hash"] if row else None


def delete_user(user_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))  # cascades
    db.commit()


# ----------------------------------------------------------------------------- venues

def venue_count() -> int:
    return get_db().execute("SELECT COUNT(*) FROM venues").fetchone()[0]


def all_venues() -> dict:
    """{venue_id: (category, lat, lon, popularity, name, owner_user_id)}"""
    rows = get_db().execute(
        "SELECT venue_id, category, latitude, longitude, popularity, name, owner_user_id FROM venues")
    return {r[0]: (r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows}


def get_venue(venue_id: str):
    return get_db().execute(
        "SELECT venue_id, category, latitude, longitude, popularity FROM venues WHERE venue_id = ?",
        (venue_id,)).fetchone()


def search_venues(user_id: int, q: str):
    """Dataset venues plus this user's own places whose category (or name) contains q.
    Other users' places are never returned. Caller orders by distance."""
    like = f"%{_like_escape(q)}%"
    return get_db().execute(
        "SELECT venue_id, category, latitude, longitude, popularity, name FROM venues "
        "WHERE (owner_user_id IS NULL OR owner_user_id = ?) "
        "AND (category LIKE ? ESCAPE '\\' COLLATE NOCASE OR name LIKE ? ESCAPE '\\' COLLATE NOCASE)",
        (user_id, like, like)).fetchall()


def upsert_own_venue(user_id: int, venue_id: str, category: str, lat: float, lon: float,
                     name: str | None) -> None:
    """A place found from the user's detected location. Visible only to that user."""
    db = get_db()
    db.execute(
        "INSERT INTO venues (venue_id, category, latitude, longitude, popularity, name, owner_user_id) "
        "VALUES (?, ?, ?, ?, 0, ?, ?) ON CONFLICT(venue_id) DO UPDATE SET category = excluded.category, "
        "name = excluded.name WHERE venues.owner_user_id = excluded.owner_user_id",
        (venue_id, category, lat, lon, name, user_id))
    db.commit()


# ----------------------------------------------------------------------------- check-ins

def last_checkin(user_id: int):
    return get_db().execute(
        "SELECT checkin_id, venue_id, ts_utc, tz_offset_min FROM checkins WHERE user_id = ? "
        "ORDER BY ts_utc DESC, checkin_id DESC LIMIT 1", (user_id,)).fetchone()


def add_checkin(user_id: int, venue_id: str, ts_utc: int, tz_offset_min: int,
                source: str = "app", predicted_rank: int | None = None) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO checkins (user_id, venue_id, ts_utc, tz_offset_min, source, predicted_rank, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, venue_id, ts_utc, tz_offset_min, source, predicted_rank, _now_iso()))
    db.commit()
    return cur.lastrowid


def copy_sample(user_id: int, rows: list) -> int:
    db = get_db()
    now = _now_iso()
    with db:
        db.executemany(
            "INSERT INTO checkins (user_id, venue_id, ts_utc, tz_offset_min, source, predicted_rank, created_at) "
            "SELECT ?, ?, ?, ?, 'sample', NULL, ? WHERE EXISTS (SELECT 1 FROM venues WHERE venue_id = ?)",
            [(user_id, v, int(ts), int(tz), now, v) for v, ts, tz in rows])
    return len(rows)


def history(user_id: int, limit: int = 1000) -> list:
    """The user's most recent `limit` check-ins (0 = all), oldest first."""
    sql = ("SELECT checkin_id, venue_id, ts_utc, tz_offset_min FROM checkins WHERE user_id = ? "
           "ORDER BY ts_utc DESC, checkin_id DESC")
    params: tuple = (user_id,)
    if limit:
        sql += " LIMIT ?"
        params = (user_id, limit)
    rows = get_db().execute(sql, params).fetchall()
    rows.reverse()
    return rows


def recent_checkins(user_id: int, limit: int) -> list:
    return get_db().execute(
        "SELECT c.checkin_id, c.venue_id, v.category, v.name, v.latitude AS lat, v.longitude AS lon, "
        "c.ts_utc, c.tz_offset_min, c.source, c.predicted_rank "
        "FROM checkins c JOIN venues v ON v.venue_id = c.venue_id "
        "WHERE c.user_id = ? ORDER BY c.ts_utc DESC, c.checkin_id DESC LIMIT ?",
        (user_id, limit)).fetchall()


def checkin_stats(user_id: int) -> dict:
    r = get_db().execute(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(source = 'app'), 0) AS own, "
        "COALESCE(SUM(source = 'app' AND predicted_rank IS NOT NULL), 0) AS scored, "
        "COALESCE(SUM(source = 'app' AND predicted_rank BETWEEN 1 AND 3), 0) AS hits_top3 "
        "FROM checkins WHERE user_id = ?", (user_id,)).fetchone()
    return {k: int(r[k]) for k in ("total", "own", "scored", "hits_top3")}


def delete_checkins(user_id: int) -> None:
    db = get_db()
    with db:
        db.execute("DELETE FROM predictions WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM checkins WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM venues WHERE owner_user_id = ?", (user_id,))


# ----------------------------------------------------------------------------- predictions

def _latest_batch(user_id: int):
    db = get_db()
    head = db.execute(
        "SELECT batch_id, context_checkin_id FROM predictions WHERE user_id = ? "
        "ORDER BY prediction_id DESC LIMIT 1", (user_id,)).fetchone()
    if head is None:
        return None, None, []
    venues = [r["venue_id"] for r in db.execute(
        "SELECT venue_id FROM predictions WHERE user_id = ? AND batch_id = ? ORDER BY rank",
        (user_id, head["batch_id"]))]
    return head["batch_id"], head["context_checkin_id"], venues


def log_predictions(user_id: int, context_checkin_id: int | None, predictions: list,
                    algorithm_version: str) -> bool:
    """Insert one batch. Skipped (returns False) when it equals the previous batch for the same
    context, so refreshing the dashboard doesn't fill the table."""
    _, prev_ctx, prev_venues = _latest_batch(user_id)
    venues = [p["venue_id"] for p in predictions]
    if prev_venues and prev_ctx == context_checkin_id and prev_venues == venues:
        return False
    batch = secrets.token_hex(8)
    now = _now_iso()
    db = get_db()
    with db:
        db.executemany(
            "INSERT INTO predictions (user_id, batch_id, context_checkin_id, rank, venue_id, score, "
            "algorithm_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(user_id, batch, context_checkin_id, p["rank"], p["venue_id"], p["score"],
              algorithm_version, now) for p in predictions])
    return True


def rank_in_last_prediction(user_id: int, context_checkin_id: int | None, venue_id: str) -> int | None:
    """Rank of venue_id in the last batch shown for the user's current place:
    1..k = predicted, 0 = missed, None = no prediction was shown for this place."""
    batch, ctx, venues = _latest_batch(user_id)
    if batch is None or ctx != context_checkin_id:
        return None
    return venues.index(venue_id) + 1 if venue_id in venues else 0


# ----------------------------------------------------------------------------- export

def export_user(user_id: int) -> dict:
    db = get_db()
    user = get_user(user_id)
    checkins = db.execute(
        "SELECT c.checkin_id, c.venue_id, v.category, v.name, v.latitude, v.longitude, c.ts_utc, "
        "c.tz_offset_min, c.source, c.predicted_rank, c.created_at FROM checkins c "
        "JOIN venues v ON v.venue_id = c.venue_id WHERE c.user_id = ? ORDER BY c.ts_utc, c.checkin_id",
        (user_id,)).fetchall()
    predictions = db.execute(
        "SELECT prediction_id, batch_id, context_checkin_id, rank, venue_id, score, algorithm_version, "
        "created_at FROM predictions WHERE user_id = ? ORDER BY prediction_id", (user_id,)).fetchall()
    return {
        "exported_at": _now_iso(),
        "user": {"email": user["email"], "created_at": user["created_at"],
                 "sample_source": user["sample_source"]},
        "checkins": [dict(r) for r in checkins],
        "predictions": [dict(r) for r in predictions],
    }


def count_rows(user_id: int) -> dict:
    db = get_db()
    return {t: db.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (user_id,)).fetchone()[0]
            for t in ("checkins", "predictions")}


# ----------------------------------------------------------------------------- login rate limit

def recent_failures(email: str, ip: str, since: int) -> tuple[int, int, int | None]:
    """(failures for this email, failures from this ip, oldest relevant attempt time)."""
    db = get_db()
    by_email = db.execute(
        "SELECT COUNT(*), MIN(attempted_at) FROM login_attempts WHERE email = ? AND attempted_at >= ?",
        (email, since)).fetchone()
    by_ip = db.execute(
        "SELECT COUNT(*), MIN(attempted_at) FROM login_attempts WHERE ip = ? AND attempted_at >= ?",
        (ip, since)).fetchone()
    oldest = [t for t in (by_email[1], by_ip[1]) if t is not None]
    return by_email[0], by_ip[0], (min(oldest) if oldest else None)


def record_failure(email: str, ip: str, at: int) -> None:
    db = get_db()
    db.execute("INSERT INTO login_attempts (email, ip, attempted_at) VALUES (?, ?, ?)", (email, ip, at))
    db.commit()


def clear_failures(email: str, before: int) -> None:
    db = get_db()
    db.execute("DELETE FROM login_attempts WHERE email = ? OR attempted_at < ?", (email, before))
    db.commit()
