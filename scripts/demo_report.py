"""Check the prediction algorithm on the three Mumbai demo people and write docs/DEMO.md.

Two kinds of check, both using the real algorithm (predictor/algorithm.py) with the app's settings:

1. Scenarios. Put a person at a place on a given weekday and time ("what if Priya is at Dadar
   Station at 08:35 on a Tuesday?") and compare the top 3 with
     - the routine answer: where their written routine (app/services/demo.py) sends them next, and
     - the data answer: where they actually went next most often, in their own history, from the
       same place in the same 3-hour time bucket.
   A scenario passes when the routine answer is in the top 3. Scenarios marked "limitation" show
   something the rule cannot see by design (e.g. Friday vs other weekdays) and are reported, not
   counted.
2. Replay. Like scripts/evaluate.py, but on each person's own history: walk their last 20% of
   check-ins in time order and record whether the next place was in the top 1/3/5.

Usage:  python scripts/demo_report.py          (prints the results, writes docs/DEMO.md)
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import load_settings  # noqa: E402
from predictor.algorithm import (  # noqa: E402
    bucket_label, build_profile, haversine_km, reason_text, score_candidates, time_bucket, top_k,
)
from predictor.types import AlgoConfig, CheckIn  # noqa: E402
from app.services import demo  # noqa: E402
from scripts.evaluate import metrics, replay  # noqa: E402

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# (person, at place, weekday 0=Mon, local hour, routine answer, kind, note)
SCENARIOS = [
    ("aarav", "aarav_home", 0, 8.75, "aarav_office", "check", "leaves for work in BKC"),
    ("aarav", "aarav_office", 0, 13.15, "jio_drive", "check", "lunch at Jio World Drive on half of weekdays"),
    ("aarav", "aarav_office", 2, 19.6, "aarav_gym", "check", "gym after work on Mon/Wed/Fri"),
    ("aarav", "aarav_home", 5, 7.2, "powai_lake", "check", "Saturday morning run"),
    ("aarav", "aarav_home", 6, 10.2, "siddhivinayak", "check", "Sunday temple visit before his parents'"),
    ("aarav", "aarav_office", 4, 19.6, "marketcity", "limitation",
     "Fridays he usually goes to Phoenix Marketcity, but the rule only knows weekday vs weekend"),
    ("priya", "priya_home", 1, 8.35, "dadar_stn", "check", "walks to the station for college"),
    ("priya", "dadar_stn", 1, 8.6, "csmt", "check", "takes the local train to CSMT"),
    ("priya", "dadar_stn", 1, 18.9, "priya_home", "check", "same station in the evening: heading home"),
    ("priya", "marine_drive", 4, 19.5, "churchgate", "check", "Friday evening: train home from Churchgate"),
    ("priya", "priya_home", 6, 7.5, "siddhivinayak", "check", "Sunday morning temple visit"),
    ("rohan", "rohan_home", 0, 7.0, "kokilaben", "check", "day-shift week: to the hospital"),
    ("rohan", "rohan_home", 0, 12.6, "kokilaben", "check", "evening-shift week: to the hospital at lunchtime"),
    ("rohan", "kokilaben", 1, 22.4, "rohan_home", "check", "after an evening shift: home"),
    ("rohan", "mount_mary", 6, 11.0, "linking_road", "check", "after Sunday mass: shopping on Linking Road"),
]

# The week after the template ends (2026-06-29 is a Monday): scenario times fall after all history.
SCENARIO_MONDAY = datetime(2026, 6, 29, tzinfo=timezone.utc)


def label(key: str) -> str:
    return demo.PLACES[key][0]


def scenario_ts(weekday: int, hour: float) -> int:
    local_midnight = SCENARIO_MONDAY.timestamp() + weekday * 86400
    return int(local_midnight + hour * 3600 - demo.IST * 60)


def data_answer(history: list, at_vid: str, bucket: int, hours: int) -> tuple[str | None, float, int]:
    """Most frequent next place after `at_vid`, counting moves whose next check-in falls in `bucket`."""
    nxt = Counter()
    for (a, _, _), (b, ts, tz) in zip(history, history[1:]):
        if a == at_vid and time_bucket(ts, tz, hours) == bucket:
            nxt[b] += 1
    if not nxt:
        return None, 0.0, 0
    vid, n = nxt.most_common(1)[0]
    return vid, n / sum(nxt.values()), sum(nxt.values())


def run_scenarios(cfg: AlgoConfig) -> list[dict]:
    names = {demo.venue_id(k): k for k in demo.PLACES}
    histories = {p: demo.template_history(p) for p in demo.PEOPLE}
    out = []
    for person, at, wd, hour, expected, kind, note in SCENARIOS:
        hist = histories[person]
        ts = scenario_ts(wd, hour)
        at_vid = demo.venue_id(at)
        checkins = [CheckIn(v, t, z) for v, t, z in hist] + [CheckIn(at_vid, ts, demo.IST)]
        profile = build_profile(checkins, cfg)
        scores = score_candidates(profile, ts, demo.IST, {}, cfg)
        preds = top_k(scores, 3, {})
        top = [names[p.venue_id] for p in preds]
        bucket = time_bucket(ts, demo.IST, cfg.time_bucket_hours)
        mode_vid, share, n = data_answer(hist, at_vid, bucket, cfg.time_bucket_hours)
        rank = top.index(expected) + 1 if expected in top else None
        out.append({
            "person": person, "at": at, "when": f"{DAY_NAMES[wd]} {int(hour):02d}:{int(hour % 1 * 60):02d}",
            "bucket": bucket_label(bucket, cfg.time_bucket_hours), "expected": expected, "kind": kind,
            "note": note, "top3": top,
            "top3_detail": [(names[p.venue_id], round(p.probability, 3),
                             reason_text(p.reason, demo.PLACES[at][1])) for p in preds],
            "rank_of_expected": rank, "passed": rank is not None,
            "data_answer": names.get(mode_vid), "data_share": round(share, 2), "data_n": n,
            "top1_matches_data": bool(top) and names.get(mode_vid) == top[0],
        })
    return out


def run_replay(cfg: AlgoConfig) -> dict:
    venues = {demo.venue_id(k): (cat, lat, lon) for k, (_, cat, lat, lon, _) in demo.PLACES.items()}
    out = {}
    for person in demo.PEOPLE:
        hist = demo.template_history(person)
        cutoff = hist[int(len(hist) * 0.8)][1]
        res = replay({person: [tuple(r) for r in hist]}, cutoff, cfg, {}, venues)
        out[person] = {m: metrics(res["ranks"][m]) for m in ("most_visited", "markov", "full")}
    return out


def people_summary() -> list[dict]:
    rows = []
    for key, p in demo.PEOPLE.items():
        hist = demo.template_history(key)
        places = Counter(v for v, _, _ in hist)
        span_km = max(haversine_km(demo.PLACES[a][2], demo.PLACES[a][3], demo.PLACES[b][2], demo.PLACES[b][3])
                      for a in demo.PLACES for b in demo.PLACES
                      if demo.venue_id(a) in places and demo.venue_id(b) in places)
        rows.append({"key": key, "name": p["name"], "role": p["role"], "area": p["area"], "story": p["story"],
                     "checkins": len(hist), "places": len(places), "days": round((hist[-1][1] - hist[0][1]) / 86400),
                     "span_km": round(span_km, 1)})
    return rows


def write_markdown(path: Path, people, scenarios, replay_res) -> None:
    passed = sum(s["passed"] for s in scenarios if s["kind"] == "check")
    total = sum(s["kind"] == "check" for s in scenarios)
    top1 = sum(s["rank_of_expected"] == 1 for s in scenarios if s["kind"] == "check")
    data_match = sum(s["top1_matches_data"] for s in scenarios if s["kind"] == "check")
    lines = [
        "<!-- Generated by scripts/demo_report.py. Do not edit by hand. -->",
        "",
        "# Mumbai demo: data and prediction check",
        "",
        "Three **made-up people** whose check-ins are traced around Mumbai for six months "
        "(`app/services/demo.py`). Places are real Mumbai landmarks (9 coordinates verified on "
        "OpenStreetMap, the rest approximate to a few hundred metres); homes, offices, gyms and "
        "cafés are invented spots in real neighbourhoods. The app moves each history forward by "
        "whole weeks so it always ends in the past week.",
        "",
        "| Person | Who | Check-ins | Places | Days | Furthest apart |",
        "|---|---|---|---|---|---|",
    ]
    for p in people:
        lines.append(f"| {p['name']} | {p['role']}. {p['area']} | {p['checkins']} | {p['places']} | "
                     f"{p['days']} | {p['span_km']} km |")
    lines += ["", "Routines: " + " ".join(f"**{p['name'].split()[0]}:** {p['story']}" for p in people), ""]
    lines += [
        "## Scenario check",
        "",
        f"**{passed} of {total} scenarios pass**: the place the routine says comes next is in the "
        f"algorithm's top 3. It is ranked **#1 in {top1} of {total}**. The algorithm's #1 also equals "
        f"the place the person actually went to most often in that situation in {data_match} of {total}.",
        "",
        "| Person | Situation | Routine says | Algorithm top 3 (likelihood) | Rank | Most frequent in data |",
        "|---|---|---|---|---|---|",
    ]
    for s in scenarios:
        top = "<br>".join(f"{i}. {label(k)} ({p:.0%})" for i, (k, p, _) in enumerate(s["top3_detail"], 1))
        rank = f"#{s['rank_of_expected']}" if s["rank_of_expected"] else "not in top 3"
        mark = ("✅ " if s["passed"] else "❌ ") if s["kind"] == "check" else "⚠️ "
        data = (f"{label(s['data_answer'])} ({s['data_share']:.0%} of {s['data_n']})"
                if s["data_answer"] else "no such move in data")
        lines.append(f"| {PEOPLE_FIRST[s['person']]} | {label(s['at'])}, {s['when']} ({s['bucket']}) | "
                     f"{label(s['expected'])}: {s['note']} | {top} | {mark}{rank} | {data} |")
    lines += [
        "",
        "⚠️ = known limitation, not counted. The time signal uses 3-hour buckets split only into weekday "
        "and weekend, so a habit tied to one weekday (Aarav's Fridays) is averaged with the other four.",
        "",
        "## Replay on each person's own history",
        "",
        "Same protocol as the main evaluation: walk the last 20% of the person's check-ins in time order; "
        "each prediction uses only earlier check-ins.",
        "",
        "| Person | Method | Test check-ins | Top-1 | Top-3 | Top-5 |",
        "|---|---|---|---|---|---|",
    ]
    names = {"most_visited": "Most visited (baseline)", "markov": "Markov only", "full": "SyncroMove (full rule)"}
    for person, res in replay_res.items():
        for m in ("most_visited", "markov", "full"):
            r = res[m]
            lines.append(f"| {PEOPLE_FIRST[person]} | {names[m]} | {r['n']} | {r['top1']:.1%} | "
                         f"{r['top3']:.1%} | {r['top5']:.1%} |")
    lines += [
        "",
        "These people follow routines written to be learnable, so their accuracy is much higher than on "
        "real people. **The real-data result is in [EVALUATION.md](EVALUATION.md); quote that one.** "
        "This page shows that the algorithm picks up the patterns it is designed to pick up.",
        "",
        "Regenerate: `python scripts/demo_report.py`",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


PEOPLE_FIRST = {k: p["name"].split()[0] for k, p in demo.PEOPLE.items()}


def main(argv: list[str] | None = None) -> int:
    cfg = AlgoConfig.from_settings(load_settings())
    people = people_summary()
    scenarios = run_scenarios(cfg)
    replay_res = run_replay(cfg)
    for p in people:
        print(f"{p['name']:<14} {p['checkins']:>4} check-ins, {p['places']:>2} places, {p['days']} days")
    print()
    for s in scenarios:
        status = "PASS" if s["passed"] else ("note" if s["kind"] == "limitation" else "FAIL")
        print(f"[{status}] {PEOPLE_FIRST[s['person']]:<6} at {label(s['at'])}, {s['when']}: "
              f"expected {label(s['expected'])} -> rank {s['rank_of_expected']}; top 3 = "
              f"{', '.join(label(k) for k in s['top3'])}; data says {label(s['data_answer']) if s['data_answer'] else '-'}")
    print()
    for person, res in replay_res.items():
        print(f"{PEOPLE_FIRST[person]:<6} replay top-3: full {res['full']['top3']:.1%}, "
              f"most visited {res['most_visited']['top3']:.1%}, markov {res['markov']['top3']:.1%}")
    out = ROOT / "docs" / "DEMO.md"
    write_markdown(out, people, scenarios, replay_res)
    print(f"\nWrote {out}")
    failed = [s for s in scenarios if s["kind"] == "check" and not s["passed"]]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
