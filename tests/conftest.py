import csv
import gzip
import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402

# 24-hex venue ids for a small test city
V = {name: f"{i:024x}" for i, name in enumerate(
    ["home", "subway", "office", "coffee", "gym", "bar", "park", "museum"], start=1)}
CATEGORIES = {"home": "Home (private)", "subway": "Subway", "office": "Office", "coffee": "Coffee Shop",
              "gym": "Gym", "bar": "Bar", "park": "Park", "museum": "Museum"}


def write_venues(path: Path) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["venue_id", "category", "latitude", "longitude", "popularity"])
    for i, (name, vid) in enumerate(V.items()):
        w.writerow([vid, CATEGORIES[name], 40.70 + i * 0.01, -74.00 + i * 0.01, 10 * (i + 1)])
    path.write_bytes(gzip.compress(buf.getvalue().encode()))


def write_samples(path: Path) -> None:
    base = 1_350_000_000
    seq = ["home", "subway", "office", "coffee", "office", "gym", "home", "subway", "office"]
    rows = [[V[n], base + i * 3600, -240] for i, n in enumerate(seq)]
    path.write_bytes(gzip.compress(json.dumps({"42": rows}).encode()))


@pytest.fixture
def app(tmp_path):
    venues = tmp_path / "venues.csv.gz"
    samples = tmp_path / "samples.json.gz"
    write_venues(venues)
    write_samples(samples)
    app = create_app({
        "TESTING": True,
        "DATABASE_PATH": tmp_path / "test.db",
        "VENUES_PATH": venues,
        "SAMPLES_PATH": samples,
        "SECRET_KEY": "test-secret",
    })
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_from(html: str) -> str:
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert m, "csrf meta tag missing"
    return m.group(1)


class Session:
    """Test helper that keeps the CSRF token and logs in through the real forms."""

    def __init__(self, client):
        self.c = client
        self.token = None

    def refresh_token(self, path="/login"):
        self.token = csrf_from(self.c.get(path).get_data(as_text=True))
        return self.token

    def register(self, email="a@example.com", password="secret123", sample=False):
        tok = self.refresh_token("/register")
        data = {"csrf_token": tok, "email": email, "password": password, "confirm": password}
        if sample:
            data["sample"] = "1"
        r = self.c.post("/register", data=data)
        if r.status_code == 302:
            self.refresh_token("/dashboard")
        return r

    def login(self, email, password, **extra):
        tok = self.refresh_token("/login")
        r = self.c.post("/login", data={"csrf_token": tok, "email": email, "password": password, **extra})
        if r.status_code == 302:
            self.refresh_token("/dashboard")
        return r

    def logout(self):
        return self.c.post("/logout", data={"csrf_token": self.token})

    def post_json(self, path, body):
        return self.c.post(path, json=body, headers={"X-CSRF-Token": self.token})


@pytest.fixture
def session(client):
    return Session(client)
