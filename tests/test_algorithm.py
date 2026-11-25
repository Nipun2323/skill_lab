import calendar
import time

import pytest

from predictor.algorithm import (
    add_checkin, build_profile, candidates_for, combine, compute_signals, rank_of, reason_text,
    score_candidates, time_bucket, top_k, weighted_totals,
)
from predictor.types import AlgoConfig, CheckIn, Profile

CFG = AlgoConfig()


def ts(y, mo, d, h, mi=0):
    return calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0))


# Worked example (§4.4). 2024-01-01 is a Monday; tz offset 0.
WORKED = [
    ("Home", ts(2024, 1, 1, 8)), ("Subway", ts(2024, 1, 1, 8, 30)), ("Office", ts(2024, 1, 1, 9)),
    ("Coffee Shop", ts(2024, 1, 1, 11)), ("Office", ts(2024, 1, 1, 11, 30)), ("Gym", ts(2024, 1, 1, 19)),
    ("Home", ts(2024, 1, 2, 7)), ("Subway", ts(2024, 1, 2, 8)), ("Office", ts(2024, 1, 2, 9)),
]
QUERY = ts(2024, 1, 3, 18, 30)  # Wednesday 18:30


def worked_profile():
    return build_profile([CheckIn(v, t, 0) for v, t in WORKED], CFG)


def test_profile_counts():
    p = worked_profile()
    assert p.count == {"Home": 2, "Subway": 2, "Office": 3, "Coffee Shop": 1, "Gym": 1}
    assert p.trans["Office"] == {"Coffee Shop": 1, "Gym": 1}
    assert p.trans["Home"] == {"Subway": 2}
    assert p.current == "Office" and p.previous == "Subway"
    assert p.recent[:3] == ["Office", "Subway", "Home"]
    assert len(p) == 9


def test_worked_example_scores_and_ranking():
    p = worked_profile()
    scores = score_candidates(p, QUERY, 0, {}, CFG)
    assert set(scores) == {"Home", "Subway", "Coffee Shop", "Gym"}
    assert round(scores["Gym"].total, 3) == 0.553
    assert round(scores["Coffee Shop"].total, 3) == 0.246
    assert round(scores["Subway"].total, 3) == 0.105
    assert round(scores["Home"].total, 3) == 0.097
    assert round(scores["Subway"].parts["recency"], 3) == 0.380
    assert round(scores["Gym"].parts["recency"], 3) == 0.193
    preds = top_k(scores, 3, {})
    assert [x.venue_id for x in preds] == ["Gym", "Coffee Shop", "Subway"]
    assert [round(x.probability * 100) for x in preds] == [55, 25, 10]
    assert [x.reason for x in preds] == ["time", "transition", "frequency"]
    assert reason_text("transition", "Office") == "You often go here after an Office"
    assert reason_text("transition", "Bar") == "You often go here after a Bar"


def test_each_signal_sums_to_one_or_is_empty():
    p = worked_profile()
    cands, signals, cold = compute_signals(p, QUERY, 0, {"Gym": 5, "Home": 1}, CFG)
    assert not cold
    for name, sig in signals.items():
        assert set(sig) <= set(cands)
        assert sig == {} or abs(sum(sig.values()) - 1) < 1e-9, name
    # a time bucket with no visits -> all zero
    _, signals, _ = compute_signals(p, ts(2024, 1, 6, 2), 0, {}, CFG)  # Saturday 02:00
    assert signals["time"] == {}


def test_current_place_never_returned():
    p = worked_profile()
    scores = score_candidates(p, QUERY, 0, {"Office": 10 ** 6}, CFG)
    assert "Office" not in scores
    assert all(x.venue_id != "Office" for x in top_k(scores, 10, {"Office": 10 ** 6}))


def test_ties_broken_by_popularity_then_id():
    # A -> B, A -> C, A -> D then back to A: B, C, D tie on everything except recency...
    # so build a history where three places tie exactly.
    hist = [CheckIn("a", 0, 0), CheckIn("x", 10, 0), CheckIn("a", 20, 0), CheckIn("y", 30, 0),
            CheckIn("a", 40, 0)]
    p = build_profile(hist, AlgoConfig(recent_window=0))
    pop = {"x": 1, "y": 5}
    scores = score_candidates(p, 100, 0, {}, AlgoConfig(recent_window=0))
    assert scores["x"].total == scores["y"].total
    assert [x.venue_id for x in top_k(scores, 2, pop)] == ["y", "x"]
    assert [x.venue_id for x in top_k(scores, 2, {})] == ["x", "y"]
    assert rank_of(scores, "x", pop) == 2 and rank_of(scores, "missing", pop) is None


def test_cold_start_returns_popular_venues():
    pop = {"p1": 50, "p2": 40, "p3": 30, "p4": 1}
    scores = score_candidates(Profile(), QUERY, 0, pop, CFG)
    preds = top_k(scores, 3, pop)
    assert [x.venue_id for x in preds] == ["p1", "p2", "p3"]
    assert all(x.reason == "popularity" for x in preds)
    # one check-in: the only visited place is current, so popular places (minus current) are shown
    p = build_profile([CheckIn("p1", 0, 0)], CFG)
    assert candidates_for(p) == []
    assert [x.venue_id for x in top_k(score_candidates(p, 10, 0, pop, CFG), 3, pop)] == ["p2", "p3", "p4"]


def test_time_buckets_midnight_and_weekend():
    # Friday 23:30 UTC with +60 min offset is Saturday 00:30 local -> weekend bucket 8
    fri = ts(2024, 1, 5, 23, 30)
    assert time_bucket(fri, 0, 3) == 7          # weekday 21-24
    assert time_bucket(fri, 60, 3) == 8         # weekend 00-03
    # Monday 01:00 UTC at -300 (New York) is Sunday 20:00 local -> weekend 18-21
    assert time_bucket(ts(2024, 1, 8, 1), -300, 3) == 8 + 6
    assert time_bucket(ts(2024, 1, 8, 1), 0, 3) == 0


def test_markov_order_2_falls_back_to_order_1():
    cfg2 = AlgoConfig(markov_order=2)
    # pair (a, b) seen twice going to c; pair (x, b) never seen -> fall back to b's row
    hist = ["a", "b", "c", "a", "b", "c", "x", "b", "d", "x", "b"]
    p = build_profile([CheckIn(v, i, 0) for i, v in enumerate(hist)], cfg2)
    assert p.trans2[("a", "b")] == {"c": 2}
    _, sig, _ = compute_signals(p, 100, 0, {}, cfg2)
    assert sig["transition"] == pytest.approx({"c": 2 / 3, "d": 1 / 3})  # order-1 row of b
    hist2 = hist[:-2] + ["a", "b"]
    p2 = build_profile([CheckIn(v, i, 0) for i, v in enumerate(hist2)], cfg2)
    _, sig2, _ = compute_signals(p2, 100, 0, {}, cfg2)
    assert sig2["transition"] == {"c": 1.0}     # pair (a, b) seen >= 2 times


def test_collapse_repeats_and_history_window():
    p = build_profile([CheckIn("a", 0, 0), CheckIn("a", 1, 0), CheckIn("b", 2, 0)], CFG)
    assert p.count == {"a": 1, "b": 1}
    # sliding window equals building from the last N check-ins
    seq = [CheckIn(v, i, 0) for i, v in enumerate("abcabdacbdab" * 5)]
    cfg = AlgoConfig(history_length=7, markov_order=2)
    windowed = build_profile(seq, cfg)
    fresh = build_profile(seq[-7:], AlgoConfig(history_length=0, markov_order=2))
    assert windowed.count == fresh.count
    assert windowed.trans == fresh.trans
    assert windowed.trans2 == fresh.trans2
    assert windowed.at_time == fresh.at_time


def test_combine_matches_weighted_totals():
    p = worked_profile()
    cands, sig, _ = compute_signals(p, QUERY, 0, {"Gym": 3}, CFG)
    totals = weighted_totals(cands, sig, CFG.weights)
    scores = combine(cands, sig, CFG.weights)
    assert {v: s.total for v, s in scores.items()} == totals
