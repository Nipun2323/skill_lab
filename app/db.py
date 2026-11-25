"""SQLite connection, schema and venue sync."""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    sample_source  TEXT NULL
);

CREATE TABLE IF NOT EXISTS venues (
    venue_id    TEXT PRIMARY KEY,
    category    TEXT NOT NULL,
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    popularity  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_venues_category ON venues(category);

CREATE TABLE IF NOT EXISTS checkins (
    checkin_id      INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    venue_id        TEXT NOT NULL REFERENCES venues(venue_id),
    ts_utc          INTEGER NOT NULL,
    tz_offset_min   INTEGER NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('app', 'sample')),
    predicted_rank  INTEGER NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkins_user_ts ON checkins(user_id, ts_utc);

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id       INTEGER PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    batch_id            TEXT NOT NULL,
    context_checkin_id  INTEGER NULL REFERENCES checkins(checkin_id) ON DELETE CASCADE,
    rank                INTEGER NOT NULL,
    venue_id            TEXT NOT NULL REFERENCES venues(venue_id),
    score               REAL NOT NULL,
    algorithm_version   TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_user ON predictions(user_id, prediction_id);

CREATE TABLE IF NOT EXISTS login_attempts (
    attempt_id    INTEGER PRIMARY KEY,
    email         TEXT COLLATE NOCASE,
    ip            TEXT,
    attempted_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts(email, attempted_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip, attempted_at);

CREATE TABLE IF NOT EXISTS app_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE_PATH"])
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def read_venues_file(path: Path) -> tuple[list[tuple], str]:
    data = Path(path).read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    text = gzip.decompress(data).decode("utf-8")
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        rows.append((r["venue_id"], r["category"], float(r["latitude"]), float(r["longitude"]),
                     int(r["popularity"])))
    return rows, checksum


def load_venues(conn: sqlite3.Connection, path: Path) -> str:
    """Sync the venues table from venues.csv.gz. Skips work if the file is unchanged.
    Returns 'loaded', 'unchanged' or 'missing'."""
    path = Path(path)
    if not path.is_file():
        return "missing"
    raw = path.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'venues_checksum'").fetchone()
    if row and row["value"] == checksum:
        return "unchanged"
    rows, _ = read_venues_file(path)
    with conn:
        conn.executemany(
            """INSERT INTO venues (venue_id, category, latitude, longitude, popularity)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(venue_id) DO UPDATE SET category = excluded.category,
                   latitude = excluded.latitude, longitude = excluded.longitude,
                   popularity = excluded.popularity""",
            rows,
        )
        conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('venues_checksum', ?)",
                     (checksum,))
    return "loaded"
