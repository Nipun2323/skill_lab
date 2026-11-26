"""Generate made-up check-in histories covering one year, at real New York venues.

Each imaginary person gets a home, a work or study place and a set of regular places chosen
from the venues in data/venues.csv.gz (near home or work). A weekly routine with random
variation (skipped days, occasional new places, a holiday break) produces their check-ins.
Nobody in the output is a real person; only the venue ids and coordinates are real.

Output has the same format as the real samples, so the app can use either:
  data/samples/synthetic_histories.json.gz   {"synthetic_01": [[venue_id, ts_utc, tz_offset_min], ...]}

Timestamps cover 358 days ending on --end (default today). The app moves a copied history
forward by whole weeks so it always ends in the past 7 days, which keeps it within one year.

Usage:  python scripts/generate_synthetic.py [--people 25] [--seed 42] [--end YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_settings  # noqa: E402
from predictor.algorithm import haversine_km  # noqa: E402

DAYS = 358
MIN_PLACES = 10

# Roles -> dataset category names to pick from
ROLE_CATEGORIES = {
    "home": ["Home (private)"],
    "office": ["Office"],
    "university": ["College Academic Building", "University", "College & University"],
    "hospital": ["Hospital", "Medical Center"],
    "subway": ["Subway"],
    "coffee": ["Coffee Shop", "Café"],
    "gym": ["Gym / Fitness Center", "Gym"],
    "grocery": ["Grocery Store", "Supermarket", "Deli / Bodega"],
    "lunch": ["Food & Drink Shop", "Sandwich Place", "Fast Food Restaurant", "Pizza Place", "Food Truck"],
    "dinner": ["American Restaurant", "Italian Restaurant", "Mexican Restaurant", "Chinese Restaurant",
               "Japanese Restaurant", "Thai Restaurant", "Indian Restaurant"],
    "bar": ["Bar", "Pub"],
    "park": ["Park"],
    "friend": ["Residential Building (Apartment / Condo)", "Home (private)"],
    "shop": ["Clothing Store", "Mall", "Department Store", "Electronics Store", "Bookstore"],
    "explore": ["Museum", "Movie Theater", "Art Gallery", "Plaza", "Music Venue", "Bakery",
                "Ice Cream Shop", "Burger Joint", "Dessert Shop", "Spa / Massage"],
}

PERSONAS = [
    # name, workplace role, work days, gym days (0=Mon), likes nightlife
    ("office worker", "office", [0, 1, 2, 3, 4], [0, 2, 4], True),
    ("student", "university", [0, 1, 2, 3, 4], [1, 3], True),
    ("hospital nurse", "hospital", [0, 1, 3, 4, 5], [2, 6], False),
    ("early riser", "office", [0, 1, 2, 3, 4], [0, 1, 2, 3, 4], False),
    ("part-time worker", "office", [1, 2, 3], [0, 4, 6], True),
]


def load_venues(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(
            (r["venue_id"], float(r["latitude"]), float(r["longitude"]), int(r["popularity"])))
    return by_cat


def ny_offset(d: date) -> int:
    """New York's UTC offset in minutes (US DST: 2nd Sunday of March to 1st Sunday of November)."""
    def nth_sunday(year, month, n):
        first = date(year, month, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    return -240 if nth_sunday(d.year, 3, 2) <= d < nth_sunday(d.year, 11, 1) else -300


class Person:
    def __init__(self, rnd: random.Random, venues: dict, persona):
        self.rnd = rnd
        self.label, work_role, self.work_days, self.gym_days, self.nightlife = persona
        self.home = rnd.choice(self.pick_all(venues, "home"))
        self.work = self.near(venues, work_role, self.home, 2, 12.0)[0]
        self.places = {
            "subway_home": self.near(venues, "subway", self.home, 1, 1.5),
            "subway_work": self.near(venues, "subway", self.work, 1, 1.5),
            "coffee": self.near(venues, "coffee", self.work, 2, 1.5),
            "gym": self.near(venues, "gym", self.home, 1, 3.0),
            "grocery": self.near(venues, "grocery", self.home, 2, 1.5),
            "lunch": self.near(venues, "lunch", self.work, 4, 1.5),
            "dinner": self.near(venues, "dinner", self.home, 4, 4.0),
            "bar": self.near(venues, "bar", self.home, 2, 3.0),
            "park": self.near(venues, "park", self.home, 2, 4.0),
            "friend": self.near(venues, "friend", self.home, 2, 8.0),
            "shop": self.near(venues, "shop", self.home, 3, 8.0),
        }
        self.explore_pool = self.near(venues, "explore", self.home, 30, 10.0)
        self.coffee_habit = rnd.uniform(0.3, 0.8)
        self.checkin_rate = rnd.uniform(0.18, 0.32)  # people don't check in everywhere they go
        self.holiday_start = rnd.randrange(40, DAYS - 20)
        self.holiday_len = rnd.randrange(5, 12)

    @staticmethod
    def pick_all(venues, role):
        pool = [v for cat in ROLE_CATEGORIES[role] for v in venues.get(cat, [])]
        if not pool:
            raise SystemExit(f"No venues for role {role!r}; is data/venues.csv.gz the NYC venue list?")
        return pool

    def near(self, venues, role, anchor, k, radius_km):
        """k distinct venues of a role, chosen at random among the nearest ones to anchor."""
        pool = self.pick_all(venues, role)
        scored = sorted(pool, key=lambda v: haversine_km(anchor[1], anchor[2], v[1], v[2]))
        close = [v for v in scored[:max(40, k * 2)] if v[0] not in (self.home[0], getattr(self, "work", (None,))[0])]
        within = [v for v in close if haversine_km(anchor[1], anchor[2], v[1], v[2]) <= radius_km] or close
        self.rnd.shuffle(within)
        return within[:k] if len(within) >= k else (within + close)[:k]

    def one(self, key):
        """Pick one of a role's places, favouring the first (a favourite)."""
        options = self.places[key]
        weights = [3 if i == 0 else 1 for i in range(len(options))]
        return self.rnd.choices(options, weights)[0]

    def day(self, d: date) -> list:
        """(hour_float, venue) visits for one day."""
        r = self.rnd
        wd = d.weekday()
        visits = [(r.uniform(6.5, 8.5), self.home)] if r.random() < 0.3 else []
        if wd in self.work_days:
            start = r.gauss(8.6, 0.4)
            visits.append((start - 0.5, self.places["subway_home"][0]))
            if r.random() < self.coffee_habit:
                visits.append((start - 0.1, self.one("coffee")))
            visits.append((start, self.work))
            if r.random() < 0.45:
                visits.append((r.uniform(12, 13.5), self.one("lunch")))
                if r.random() < 0.7:
                    visits.append((r.uniform(13.5, 14), self.work))
            end = r.gauss(17.8, 0.6)
            visits.append((end, self.places["subway_work"][0]))
            if wd in self.gym_days and r.random() < 0.8:
                visits.append((end + 0.7, self.places["gym"][0]))
            if r.random() < 0.25:
                visits.append((end + 1.2, self.one("grocery")))
            if wd == 4 and self.nightlife and r.random() < 0.55:
                visits.append((r.uniform(20, 22), self.one("bar")))
            elif r.random() < 0.2:
                visits.append((r.uniform(19, 20.5), self.one("dinner")))
            visits.append((r.uniform(21, 23.5), self.home))
        else:
            if wd in self.gym_days and r.random() < 0.7:
                visits.append((r.uniform(9, 11), self.places["gym"][0]))
            if r.random() < 0.5:
                visits.append((r.uniform(11, 13), self.one("dinner")))  # brunch
            if r.random() < 0.45:
                visits.append((r.uniform(13, 16), self.one("park")))
            if r.random() < 0.3:
                visits.append((r.uniform(14, 17), self.one("shop")))
            if r.random() < 0.25:
                visits.append((r.uniform(15, 18), r.choice(self.explore_pool)))  # somewhere new
            if r.random() < 0.3:
                visits.append((r.uniform(17, 19), self.one("grocery")))
            if r.random() < 0.3:
                visits.append((r.uniform(18, 21), self.one("friend")))
            if wd == 5 and self.nightlife and r.random() < 0.5:
                visits.append((r.uniform(21, 23.5), self.one("bar")))
            visits.append((r.uniform(22, 23.9), self.home))
        visits.sort(key=lambda x: x[0])
        return visits

    def history(self, end: date) -> list:
        start = end - timedelta(days=DAYS - 1)
        out = []
        for i in range(DAYS):
            d = start + timedelta(days=i)
            if self.holiday_start <= i < self.holiday_start + self.holiday_len:
                continue  # away on holiday: no check-ins
            tz = ny_offset(d)
            for hour, venue in self.day(d):
                if self.rnd.random() > self.checkin_rate:
                    continue
                hour = min(max(hour, 0.0), 23.98)
                local = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(hours=hour)
                ts = int(local.timestamp()) - tz * 60 + self.rnd.randrange(0, 60)
                if out and out[-1][0] == venue[0]:
                    continue  # back-to-back at the same place counts once
                if out and ts <= out[-1][1]:
                    ts = out[-1][1] + 60
                out.append([venue[0], ts, tz])
        return out


def generate(venues: dict, people: int, seed: int, end: date) -> dict:
    rnd = random.Random(seed)
    result = {}
    i = 0
    while len(result) < people:
        i += 1
        person = Person(rnd, venues, PERSONAS[(i - 1) % len(PERSONAS)])
        rows = person.history(end)
        if len({r[0] for r in rows}) >= MIN_PLACES:
            result[f"synthetic_{len(result) + 1:02d}"] = rows
        if i > people * 5:
            raise SystemExit("Could not generate enough histories with at least 10 places.")
    return result


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venues", type=Path, default=settings["VENUES_PATH"])
    ap.add_argument("--out", type=Path, default=root / "data" / "samples" / "synthetic_histories.json.gz")
    ap.add_argument("--people", type=int, default=25)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--end", type=date.fromisoformat, default=datetime.now(timezone.utc).date() - timedelta(days=1))
    args = ap.parse_args(argv)

    venues = load_venues(args.venues)
    data = generate(venues, args.people, args.seed, args.end)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(json.dumps(data, separators=(",", ":")).encode("utf-8"))

    counts = [len(v) for v in data.values()]
    places = [len({r[0] for r in v}) for v in data.values()]
    spans = [(v[-1][1] - v[0][1]) / 86400 for v in data.values()]
    fmt = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    print(f"Wrote {args.out} ({len(data)} made-up people, {sum(counts):,} check-ins)")
    print(f"  check-ins per person: {min(counts)}–{max(counts)} (median {sorted(counts)[len(counts) // 2]})")
    print(f"  different places per person: {min(places)}–{max(places)}")
    print(f"  span: {min(spans):.0f}–{max(spans):.0f} days, "
          f"{fmt(min(v[0][1] for v in data.values()))} to {fmt(max(v[-1][1] for v in data.values()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
