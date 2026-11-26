"""Demo data: three made-up people whose check-ins are traced around Mumbai for six months.

* Places are real Mumbai landmarks. Coordinates marked verified=True were looked up on
  OpenStreetMap (Nominatim); the rest are approximate (from the author's knowledge, typically
  within a few hundred metres). Homes, offices, gyms and cafés are invented spots in real
  neighbourhoods, named after the area ("Home · Hiranandani Gardens").
* People are invented. Each follows a weekly routine with random variation, written below, so a
  reviewer can read the routine and check whether a prediction makes sense.
* Histories are generated for a fixed 26-week template ending ANCHOR_END and moved forward by whole
  weeks when seeded, so they always end in the past 7 days and keep every visit's weekday and time.

Seeding is idempotent: places are upserted; a demo person's history is rebuilt when it is missing,
out of date, or the generator version changes (see ensure_demo).
"""
from __future__ import annotations

import hashlib
import random
import time
from datetime import date, datetime, timedelta, timezone

from .samples import shift_to_recent

GENERATOR_VERSION = "mumbai-v1"
IST = 330  # India Standard Time, UTC+05:30, no daylight saving
ANCHOR_END = date(2026, 6, 28)  # last day of the template (a Sunday)
WEEKS = 26
DAYS = WEEKS * 7  # 182 days, about six months

# key: (name, category, lat, lon, verified)
PLACES = {
    # --- verified on OpenStreetMap
    "gateway": ("Gateway of India", "Monument / Landmark", 18.9219661, 72.8345657, True),
    "csmt": ("Chhatrapati Shivaji Maharaj Terminus", "Train Station", 18.9398560, 72.8355191, True),
    "churchgate": ("Churchgate Station", "Train Station", 18.9337168, 72.8274613, True),
    "nariman": ("Nariman Point", "Scenic Lookout", 18.9253433, 72.8180282, True),
    "kala_ghoda": ("Kala Ghoda", "Art Gallery", 18.9284581, 72.8325994, True),
    "colaba": ("Colaba Causeway", "Market", 18.9193269, 72.8295075, True),
    "xaviers": ("St. Xavier's College", "College Academic Building", 18.9429649, 72.8314839, True),
    "crawford": ("Crawford Market", "Market", 18.9473499, 72.8345108, True),
    "oval": ("Oval Maidan", "Park", 18.9295936, 72.8289366, True),
    # --- approximate (Marine Drive was looked up, but OpenStreetMap returned the road's northern
    # end at Chowpatty, so this is a point on the promenade instead)
    "marine_drive": ("Marine Drive Promenade", "Scenic Lookout", 18.9440, 72.8228, False),
    "asiatic": ("Asiatic Society Library", "Library", 18.9318, 72.8361, False),
    "haji_ali": ("Haji Ali Dargah", "Mosque", 18.9827, 72.8089, False),
    "palladium": ("Phoenix Palladium, Lower Parel", "Mall", 18.9947, 72.8258, False),
    "worli": ("Worli Sea Face", "Scenic Lookout", 19.0065, 72.8155, False),
    "siddhivinayak": ("Siddhivinayak Temple", "Temple", 19.0169, 72.8303, False),
    "dadar_stn": ("Dadar Station", "Train Station", 19.0183, 72.8430, False),
    "shivaji_park": ("Shivaji Park", "Park", 19.0270, 72.8383, False),
    "kings_circle": ("King's Circle, Matunga", "South Indian Restaurant", 19.0275, 72.8567, False),
    "carter": ("Carter Road Promenade", "Scenic Lookout", 19.0648, 72.8220, False),
    "bandstand": ("Bandstand Promenade", "Scenic Lookout", 19.0466, 72.8197, False),
    "mount_mary": ("Mount Mary Basilica", "Church", 19.0468, 72.8225, False),
    "hill_road": ("Hill Road, Bandra", "Shopping Street", 19.0550, 72.8330, False),
    "linking_road": ("Linking Road, Bandra", "Clothing Store", 19.0640, 72.8345, False),
    "lilavati": ("Lilavati Hospital", "Hospital", 19.0510, 72.8290, False),
    "jio_drive": ("Jio World Drive, BKC", "Mall", 19.0640, 72.8655, False),
    "marketcity": ("Phoenix Marketcity, Kurla", "Mall", 19.0866, 72.8890, False),
    "airport": ("Mumbai Airport, Terminal 2", "Airport", 19.0990, 72.8740, False),
    "juhu": ("Juhu Beach", "Beach", 19.0988, 72.8267, False),
    "iskcon": ("ISKCON Temple, Juhu", "Temple", 19.1131, 72.8268, False),
    "lokhandwala": ("Lokhandwala Market", "Market", 19.1415, 72.8290, False),
    "kokilaben": ("Kokilaben Dhirubhai Ambani Hospital", "Hospital", 19.1310, 72.8250, False),
    "versova": ("Versova Beach", "Beach", 19.1370, 72.8120, False),
    "powai_lake": ("Powai Lake", "Lake", 19.1270, 72.9050, False),
    "iitb": ("IIT Bombay", "University", 19.1334, 72.9133, False),
    "r_city": ("R City Mall, Ghatkopar", "Mall", 19.0996, 72.9163, False),
    # --- invented private spots in real neighbourhoods
    "aarav_home": ("Home · Hiranandani Gardens", "Home (private)", 19.1176, 72.9060, False),
    "aarav_office": ("Office · Bandra Kurla Complex", "Office", 19.0660, 72.8680, False),
    "aarav_gym": ("Gym · Powai", "Gym / Fitness Center", 19.1195, 72.9085, False),
    "aarav_cafe": ("Café · Hiranandani", "Café", 19.1160, 72.9075, False),
    "aarav_parents": ("Parents' home · Dadar", "Home (private)", 19.0225, 72.8400, False),
    "priya_home": ("Home · Dadar West", "Home (private)", 19.0240, 72.8365, False),
    "priya_friend": ("Friend's home · Matunga", "Residential Building (Apartment / Condo)", 19.0290, 72.8520, False),
    "rohan_home": ("Home · Pali Hill, Bandra", "Home (private)", 19.0680, 72.8275, False),
    "rohan_gym": ("Gym · Bandra West", "Gym / Fitness Center", 19.0610, 72.8300, False),
    "rohan_cafe": ("Café · Bandra West", "Café", 19.0580, 72.8290, False),
}

PEOPLE = {
    "aarav": {
        "name": "Aarav Mehta",
        "email": "aarav@demo.syncromove",
        "role": "Software engineer",
        "area": "Lives in Powai, works in Bandra Kurla Complex",
        "story": "Weekday commute to BKC, gym on Mon/Wed/Fri, lake walks on Tue/Thu, Saturday "
                 "morning runs, Sundays with his parents in Dadar, a work trip by air about every 5 weeks.",
    },
    "priya": {
        "name": "Priya Nair",
        "email": "priya@demo.syncromove",
        "role": "College student",
        "area": "Lives in Dadar, studies at St. Xavier's College",
        "story": "Local train Dadar → CSMT on weekdays, library or Oval Maidan after class, Friday "
                 "evenings on Marine Drive, Sunday temple visits and Shivaji Park.",
    },
    "rohan": {
        "name": "Rohan Kapoor",
        "email": "rohan@demo.syncromove",
        "role": "Doctor",
        "area": "Lives in Bandra, works at Kokilaben Hospital, Andheri",
        "story": "Alternating day and evening shifts every other week (Mon–Sat), morning jogs on "
                 "Carter Road, Sunday mass at Mount Mary then Linking Road and Juhu Beach.",
    },
}


def venue_id(key: str) -> str:
    return hashlib.sha1(f"mumbai-demo:{key}".encode()).hexdigest()[:24]


# ----------------------------------------------------------------------------- routines
# Each returns [(local_hour, place_key), ...] for one day. r is the person's Random.

def _aarav(r: random.Random, d: date, week: int) -> list:
    wd = d.weekday()
    v = []
    if wd < 5:
        if week % 5 == 2 and wd in (3, 4):  # work trip: fly out Thursday, away Friday
            return [(r.gauss(7.0, 0.3), "aarav_home"), (r.gauss(8.2, 0.3), "airport")] if wd == 3 else []
        v.append((r.gauss(8.6, 0.2), "aarav_home"))
        if r.random() < 0.45:
            v.append((r.gauss(9.0, 0.15), "aarav_cafe"))
        v.append((r.gauss(10.0, 0.3), "aarav_office"))
        if r.random() < 0.5:
            v.append((r.gauss(13.2, 0.2), "jio_drive"))
            v.append((r.gauss(14.2, 0.2), "aarav_office"))
        out = r.gauss(18.9, 0.35)
        if wd in (0, 2) and r.random() < 0.85:
            v.append((out + 0.8, "aarav_gym"))
        elif wd == 4 and r.random() < 0.6:
            v.append((out + 0.7, "marketcity"))
        elif wd == 4 and r.random() < 0.6:
            v.append((out + 0.8, "aarav_gym"))
        elif wd in (1, 3) and r.random() < 0.6:
            v.append((out + 0.9, "powai_lake"))
        v.append((r.gauss(21.6, 0.4), "aarav_home"))
    elif wd == 5:
        if week % 5 == 2:  # back from the work trip
            v.append((r.gauss(11.0, 0.5), "airport"))
            v.append((r.gauss(12.5, 0.4), "aarav_home"))
            return v
        if r.random() < 0.75:
            v.append((r.gauss(7.3, 0.25), "powai_lake"))
            v.append((r.gauss(8.5, 0.3), "aarav_home"))
        if r.random() < 0.45:
            v.append((r.gauss(13.0, 0.8), "r_city"))
        if r.random() < 0.2:
            v.append((r.gauss(17.5, 0.5), "iitb"))
        if r.random() < 0.3:
            v.append((r.gauss(20.0, 0.6), "marketcity"))
        v.append((r.gauss(22.3, 0.4), "aarav_home"))
    else:
        if r.random() < 0.55:
            v.append((r.gauss(10.4, 0.3), "siddhivinayak"))
        if r.random() < 0.8:
            v.append((r.gauss(11.5, 0.4), "aarav_parents"))
            if r.random() < 0.3:
                v.append((r.gauss(16.0, 0.5), "shivaji_park"))
        if r.random() < 0.25:
            v.append((r.gauss(18.0, 0.5), "juhu"))
        v.append((r.gauss(21.0, 0.4), "aarav_home"))
    return v


def _priya(r: random.Random, d: date, week: int) -> list:
    wd = d.weekday()
    v = []
    if wd < 5:
        if r.random() < 0.35:
            v.append((r.gauss(7.0, 0.2), "shivaji_park"))
        v.append((r.gauss(8.1, 0.15), "priya_home"))
        v.append((r.gauss(8.5, 0.12), "dadar_stn"))
        if r.random() < 0.7:
            v.append((r.gauss(9.1, 0.12), "csmt"))
        v.append((r.gauss(9.4, 0.12), "xaviers"))
        if r.random() < 0.3:
            v.append((r.gauss(13.0, 0.2), "kala_ghoda"))
            v.append((r.gauss(13.8, 0.2), "xaviers"))
        if wd == 4 and r.random() < 0.75:  # Friday evening with friends
            v.append((r.gauss(17.0, 0.3), "marine_drive"))
            v.append((r.gauss(19.6, 0.3), "churchgate"))
        else:
            if wd in (0, 2) and r.random() < 0.55:
                v.append((r.gauss(16.3, 0.3), "asiatic"))
            elif wd in (1, 3) and r.random() < 0.45:
                v.append((r.gauss(16.3, 0.3), "oval"))
            elif r.random() < 0.15:
                v.append((r.gauss(16.0, 0.3), "crawford"))
            v.append((r.gauss(18.0, 0.25), "csmt"))
        v.append((r.gauss(18.9, 0.25) + (1.6 if wd == 4 and v[-1][1] == "churchgate" else 0), "dadar_stn"))
        if wd == 2 and r.random() < 0.35:
            v.append((v[-1][0] + 0.5, "kings_circle"))
        v.append((v[-1][0] + 0.6, "priya_home"))
    elif wd == 5:
        if r.random() < 0.5:
            v.append((r.gauss(11.5, 0.5), "colaba"))
            if r.random() < 0.6:
                v.append((r.gauss(13.0, 0.4), "gateway"))
        elif r.random() < 0.5:
            v.append((r.gauss(13.0, 0.6), "palladium"))
        if r.random() < 0.45:
            v.append((r.gauss(17.5, 0.5), "priya_friend"))
        v.append((r.gauss(21.0, 0.5), "priya_home"))
    else:
        if r.random() < 0.65:
            v.append((r.gauss(7.8, 0.2), "siddhivinayak"))
            if r.random() < 0.6:
                v.append((r.gauss(9.0, 0.25), "shivaji_park"))
        v.append((r.gauss(10.5, 0.4), "priya_home"))
        if r.random() < 0.4:
            v.append((r.gauss(18.0, 0.4), "worli"))
        elif r.random() < 0.25:
            v.append((r.gauss(17.0, 0.4), "haji_ali"))
        v.append((r.gauss(21.0, 0.4), "priya_home"))
    return v


def _rohan(r: random.Random, d: date, week: int) -> list:
    wd = d.weekday()
    v = []
    day_shift = week % 2 == 0
    if wd < 6:
        if day_shift:
            if r.random() < 0.55:
                v.append((r.gauss(6.3, 0.15), "carter"))
            v.append((r.gauss(7.0, 0.15), "rohan_home"))
            v.append((r.gauss(7.8, 0.15), "kokilaben"))
            after = r.gauss(17.4, 0.3)
            if wd == 3 and r.random() < 0.6:
                v.append((after + 0.6, "lilavati"))
            elif wd in (0, 2, 4) and r.random() < 0.7:
                v.append((after + 0.7, "rohan_gym"))
            elif r.random() < 0.3:
                v.append((after + 0.5, "lokhandwala"))
            v.append((r.gauss(20.0, 0.3), "rohan_home"))
            if r.random() < 0.2:
                v.append((r.gauss(21.0, 0.3), "hill_road"))
        else:
            if r.random() < 0.65:
                v.append((r.gauss(9.0, 0.3), "rohan_gym"))
            if r.random() < 0.5:
                v.append((r.gauss(11.0, 0.3), "rohan_cafe"))
            v.append((r.gauss(12.4, 0.2), "rohan_home"))
            v.append((r.gauss(13.5, 0.15), "kokilaben"))
            if r.random() < 0.15:
                v.append((r.gauss(22.3, 0.2), "versova"))
            v.append((r.gauss(22.8, 0.25), "rohan_home"))
    else:  # Sunday off
        if r.random() < 0.5:
            v.append((r.gauss(8.0, 0.3), "bandstand"))
        if r.random() < 0.7:
            v.append((r.gauss(9.8, 0.25), "mount_mary"))
            if r.random() < 0.6:
                v.append((r.gauss(12.5, 0.4), "linking_road"))
        if r.random() < 0.5:
            v.append((r.gauss(17.3, 0.4), "juhu"))
            if r.random() < 0.35:
                v.append((r.gauss(19.0, 0.3), "iskcon"))
        v.append((r.gauss(21.0, 0.4), "rohan_home"))
    return v


ROUTINES = {"aarav": _aarav, "priya": _priya, "rohan": _rohan}
CHECKIN_RATE = {"aarav": 0.85, "priya": 0.85, "rohan": 0.85}
HOLIDAY = {"aarav": (60, 7), "priya": (95, 9), "rohan": (130, 6)}  # (start day, length)


def template_history(person: str, end: date = ANCHOR_END) -> list:
    """[[venue_id, ts_utc, tz_offset_min], ...] for 182 days ending `end`. Deterministic."""
    r = random.Random(f"{GENERATOR_VERSION}:{person}")
    start = end - timedelta(days=DAYS - 1)
    hol_start, hol_len = HOLIDAY[person]
    rows: list = []
    for i in range(DAYS):
        d = start + timedelta(days=i)
        visits = ROUTINES[person](r, d, i // 7)
        if hol_start <= i < hol_start + hol_len:
            continue  # away: no check-ins
        midnight = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) - IST * 60
        for hour, key in sorted(visits, key=lambda x: x[0]):
            if r.random() > CHECKIN_RATE[person]:
                continue
            ts = midnight + int(min(max(hour, 0.0), 23.98) * 3600)
            vid = venue_id(key)
            if rows and rows[-1][0] == vid:
                continue  # back-to-back at the same place counts once
            if rows and ts <= rows[-1][1]:
                ts = rows[-1][1] + 120
            rows.append([vid, ts, IST])
    return rows


def history(person: str, now: int | None = None) -> list:
    """The template moved forward by whole weeks so it ends in the 7 days before `now`."""
    return shift_to_recent(template_history(person), int(now if now is not None else time.time()))


# ----------------------------------------------------------------------------- seeding

def ensure_demo(conn, now: int | None = None, force: bool = False, only: str | None = None) -> dict:
    """Upsert the Mumbai places and (re)build the demo people. Returns {person: user_id}."""
    now = int(now if now is not None else time.time())
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.executemany(
        "INSERT INTO venues (venue_id, category, latitude, longitude, popularity, name, owner_user_id) "
        "VALUES (?, ?, ?, ?, 0, ?, NULL) ON CONFLICT(venue_id) DO UPDATE SET category = excluded.category, "
        "latitude = excluded.latitude, longitude = excluded.longitude, name = excluded.name",
        [(venue_id(k), cat, lat, lon, name) for k, (name, cat, lat, lon, _) in PLACES.items()])
    ids = {}
    for key, p in PEOPLE.items():
        row = conn.execute("SELECT user_id, sample_source FROM users WHERE email = ?", (p["email"],)).fetchone()
        source = f"demo:{key}:{GENERATOR_VERSION}"
        if row is None:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, created_at, sample_source) VALUES (?, ?, ?, ?)",
                (p["email"], "demo-account:no-password-login", created, source))
            uid, rebuild = cur.lastrowid, True
        else:
            uid = row[0]
            last = conn.execute("SELECT MAX(ts_utc) FROM checkins WHERE user_id = ? AND source = 'sample'",
                                (uid,)).fetchone()[0]
            rebuild = row[1] != source or last is None or last < now - 8 * 86400
        if force and (only is None or only == key):
            rebuild = True
        if rebuild:
            conn.execute("DELETE FROM predictions WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM checkins WHERE user_id = ?", (uid,))
            conn.executemany(
                "INSERT INTO checkins (user_id, venue_id, ts_utc, tz_offset_min, source, predicted_rank, created_at) "
                "VALUES (?, ?, ?, ?, 'sample', NULL, ?)",
                [(uid, v, ts, tz, created) for v, ts, tz in history(key, now)])
            conn.execute("UPDATE users SET sample_source = ? WHERE user_id = ?", (source, uid))
        ids[key] = uid
    conn.commit()
    return ids


def person_for_user(sample_source: str | None) -> str | None:
    if sample_source and sample_source.startswith("demo:"):
        key = sample_source.split(":")[1]
        return key if key in PEOPLE else None
    return None
