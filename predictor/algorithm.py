"""SyncroMove next-place prediction: a fixed, explainable scoring rule.

No training, no fitted parameters. For one user:

1. PROFILE    counts from the user's own history (visits, a->b transitions, time-of-day visits,
              the last few places).
2. CANDIDATES every place the user has visited except the current one
              (no usable history: the most popular places in the city).
3. SIGNALS    five distributions over the candidates, each summing to 1 (or all zero):
              transition, time, frequency, recency, popularity.
4. SCORE      weighted sum with the constants from config.py.
5. RANK       highest score first; ties -> higher city popularity -> venue id.
6. REASON     the signal with the largest weighted contribution.

Pure functions, no I/O. The web app and scripts/evaluate.py both call these.
"""
from __future__ import annotations

import heapq
import math
import time
from collections import deque

from .types import AlgoConfig, CheckIn, Prediction, Profile, Score

SIGNALS = ("transition", "time", "frequency", "recency", "popularity")

REASON_TEXT = {
    "transition": "You often go here after {article} {category}",
    "time": "You usually go here around this time",
    "frequency": "One of your most-visited places",
    "recency": "You've been here recently",
    "popularity": "Popular place in the city",
}


# ----------------------------------------------------------------------------- helpers

def time_bucket(ts_utc: int, tz_offset_min: int, hours: int = 3) -> int:
    """Bucket of the *local* time: (hour // hours), plus 24/hours if Saturday or Sunday."""
    local = time.gmtime(ts_utc + tz_offset_min * 60)
    per_day = 24 // hours
    weekend = 1 if local.tm_wday >= 5 else 0
    return local.tm_hour // hours + per_day * weekend


def bucket_label(bucket: int, hours: int = 3) -> str:
    per_day = 24 // hours
    kind = "weekend" if bucket >= per_day else "weekday"
    start = (bucket % per_day) * hours
    return f"{kind} {start:02d}:00-{start + hours:02d}:00"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def reason_text(code: str, current_category: str | None = None) -> str:
    if code == "transition":
        category = current_category or "your last place"
        return REASON_TEXT[code].format(article=article(category), category=category)
    return REASON_TEXT.get(code, REASON_TEXT["popularity"])


def _inc(table: dict, key, amount: int = 1) -> None:
    table[key] = table.get(key, 0) + amount


def _dec(table: dict, key) -> None:
    n = table[key] - 1
    if n:
        table[key] = n
    else:
        del table[key]


# ----------------------------------------------------------------------------- profile

def add_checkin(profile: Profile, c: CheckIn, cfg: AlgoConfig) -> None:
    """Add one check-in (newest) to the profile. O(1) apart from the 20-item recent list."""
    v = c.venue_id
    if cfg.collapse_repeats and profile.window and profile.window[-1][0].venue_id == v:
        return
    prev, prev2 = profile.current, profile.previous
    bucket = time_bucket(c.ts_utc, c.tz_offset_min, cfg.time_bucket_hours)

    _inc(profile.count, v)
    _inc(profile.at_time.setdefault(bucket, {}), v)
    if prev is not None:
        _inc(profile.trans.setdefault(prev, {}), v)
        if cfg.markov_order >= 2 and prev2 is not None:
            _inc(profile.trans2.setdefault((prev2, prev), {}), v)

    profile.window.append((c, bucket))
    profile.recent.insert(0, v)
    del profile.recent[cfg.recent_window:]

    if cfg.history_length and len(profile.window) > cfg.history_length:
        _evict_oldest(profile, cfg)


def _evict_oldest(profile: Profile, cfg: AlgoConfig) -> None:
    """Drop the oldest check-in so the profile covers only the last HISTORY_LENGTH visits."""
    c0, b0 = profile.window.popleft()
    v0 = c0.venue_id
    _dec(profile.count, v0)
    _dec(profile.at_time[b0], v0)
    if not profile.at_time[b0]:
        del profile.at_time[b0]
    if profile.window:
        v1 = profile.window[0][0].venue_id
        _dec(profile.trans[v0], v1)
        if not profile.trans[v0]:
            del profile.trans[v0]
        if cfg.markov_order >= 2 and len(profile.window) >= 2:
            v2 = profile.window[1][0].venue_id
            row = profile.trans2[(v0, v1)]
            _dec(row, v2)
            if not row:
                del profile.trans2[(v0, v1)]


def build_profile(checkins: list[CheckIn], cfg: AlgoConfig) -> Profile:
    """One pass over the history, oldest first. Keeps at most cfg.history_length visits."""
    profile = Profile()
    for c in checkins:
        add_checkin(profile, c, cfg)
    return profile


# ----------------------------------------------------------------------------- scoring

def _normalised(raw: dict, candidates) -> dict:
    total = 0.0
    kept = {}
    for v in candidates:
        x = raw.get(v)
        if x:
            kept[v] = x
            total += x
    if total <= 0:
        return {}
    return {v: x / total for v, x in kept.items()}


def popular_venues(popularity: dict, n: int, exclude: str | None = None) -> list[str]:
    best = heapq.nsmallest(n + 1, popularity, key=lambda v: (-popularity[v], v))
    return [v for v in best if v != exclude][:n]


def candidates_for(profile: Profile) -> list[str]:
    current = profile.current
    return [v for v in profile.count if v != current]


def compute_signals(profile: Profile, query_ts: int, query_tz: int, popularity: dict,
                    cfg: AlgoConfig) -> tuple[list[str], dict[str, dict], bool]:
    """Return (candidates, {signal: {venue: value}}, cold_start).

    Each signal dict holds only non-zero values and sums to 1 over the candidates,
    or is empty when the signal has no data.
    """
    current = profile.current
    candidates = candidates_for(profile)
    cold_start = not candidates
    if cold_start:
        candidates = popular_venues(popularity, cfg.cold_start_candidates, exclude=current)

    signals: dict[str, dict] = {}

    row = None
    if current is not None:
        if cfg.markov_order >= 2 and profile.previous is not None:
            row2 = profile.trans2.get((profile.previous, current))
            if row2 and sum(row2.values()) >= 2:
                row = row2
        if row is None:
            row = profile.trans.get(current)
    signals["transition"] = _normalised(row or {}, candidates)

    bucket = time_bucket(query_ts, query_tz, cfg.time_bucket_hours)
    signals["time"] = _normalised(profile.at_time.get(bucket, {}), candidates)

    signals["frequency"] = _normalised(profile.count, candidates)

    rec: dict[str, float] = {}
    w = 1.0
    for v in profile.recent:
        rec[v] = rec.get(v, 0.0) + w
        w *= cfg.recent_decay
    signals["recency"] = _normalised(rec, candidates)

    signals["popularity"] = _normalised(popularity, candidates)
    return candidates, signals, cold_start


def combine(candidates: list[str], signals: dict[str, dict], weights: dict[str, float]) -> dict:
    """Weighted sum of the signals for every candidate -> {venue: Score}."""
    scores = {}
    for v in candidates:
        parts = {name: signals[name].get(v, 0.0) for name in SIGNALS}
        contrib = {name: weights.get(name, 0.0) * parts[name] for name in SIGNALS}
        scores[v] = Score(total=sum(contrib.values()), parts=parts, contrib=contrib)
    return scores


def score_candidates(profile: Profile, query_time: int, query_tz: int, popularity: dict,
                     cfg: AlgoConfig) -> dict:
    candidates, signals, _ = compute_signals(profile, query_time, query_tz, popularity, cfg)
    return combine(candidates, signals, cfg.weights)


def _rank_key(v: str, total: float, popularity: dict):
    return (-total, -popularity.get(v, 0), v)


def reason_code(score: Score) -> str:
    best, best_val = "popularity", 0.0
    for name in SIGNALS:  # fixed order breaks exact ties
        if score.contrib[name] > best_val:
            best, best_val = name, score.contrib[name]
    return best


def top_k(scores: dict, k: int, popularity: dict) -> list[Prediction]:
    total = sum(s.total for s in scores.values())
    ranked = heapq.nsmallest(k, scores, key=lambda v: _rank_key(v, scores[v].total, popularity))
    out = []
    for v in ranked:
        s = scores[v]
        out.append(Prediction(
            venue_id=v,
            score=s.total,
            probability=(s.total / total) if total > 0 else 0.0,
            reason=reason_code(s),
            signals=dict(s.parts),
        ))
    return out


def rank_of(scores: dict, venue_id: str, popularity: dict) -> int | None:
    """1-based rank of venue_id among the scored candidates, None if it is not a candidate."""
    target = scores.get(venue_id)
    if target is None:
        return None
    key = _rank_key(venue_id, target.total, popularity)
    better = sum(1 for v, s in scores.items() if _rank_key(v, s.total, popularity) < key)
    return better + 1
