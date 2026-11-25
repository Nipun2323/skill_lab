"""Measure the SyncroMove algorithm on the NYC data. Measuring only: nothing is learned or tuned.

Protocol
  * All de-duplicated check-ins are sorted by time; the last EVAL_TEST_FRACTION of them form the
    test window. Each user's sequence then has back-to-back repeats collapsed.
  * City popularity is counted only from check-ins before the test window.
  * For each user, walk the check-ins in order. For every check-in inside the test window that
    has an earlier check-in, the profile holds only that user's earlier check-ins; run the
    algorithm with the real check-in's time as the query time and record the rank of the place
    the user actually went to (a miss if it is not a candidate). Then add the check-in.

Usage:  python scripts/evaluate.py [--data PATH] [--out docs/evaluation_results.json]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ALGORITHM_VERSION, load_settings  # noqa: E402
from predictor.algorithm import (  # noqa: E402
    add_checkin, compute_signals, haversine_km, ranked_venues, rank_in_totals, weighted_totals,
)
from predictor.types import AlgoConfig, CheckIn, Profile  # noqa: E402
from scripts.common import clean, load_raw, sequences  # noqa: E402
from scripts.import_dataset import venue_table  # noqa: E402

# §5.2 of the project spec: a validated run of this algorithm. Ours should land within ±1 point.
REFERENCE = {
    "test_checkins": 42_659,
    "methods": {
        "popularity": {"top1": 0.005, "top3": 0.012, "top5": 0.016, "mrr": 0.015},
        "most_visited": {"top1": 0.132, "top3": 0.249, "top5": 0.316, "mrr": 0.220},
        "markov": {"top1": 0.145, "top3": 0.275, "top5": 0.341, "mrr": 0.236},
        "full_no_time": {"top1": 0.148, "top3": 0.274, "top5": 0.338, "mrr": 0.238},
        "full": {"top1": 0.176, "top3": 0.310, "top5": 0.373, "mrr": 0.267},
    },
    "full_extras": {"top3_category": 0.474, "top3_within_1km": 0.615,
                    "top3_by_history": {"<50": 0.214, "50-199": 0.270, "200+": 0.365}},
}

METHOD_LABELS = {
    "popularity": "City popularity only",
    "most_visited": "Baseline: user's most visited",
    "markov": "Markov only (order 1, back-off to frequency)",
    "full_no_time": "SyncroMove algorithm, W_TIME = 0",
    "full": "SyncroMove algorithm",
}

HISTORY_BANDS = (("<50", 0, 49), ("50-199", 50, 199), ("200+", 200, math.inf))


def method_weights(cfg: AlgoConfig) -> dict[str, dict[str, float]]:
    return {
        "most_visited": {"frequency": 1.0},
        # transition first; frequency (scaled far below any transition step) breaks ties,
        # which is the back-off when the current place has no recorded transitions
        "markov": {"transition": 1.0, "frequency": 1e-6},
        "full_no_time": {**cfg.weights, "time": 0.0},
        "full": cfg.weights,
    }


def cutoff_time(rows, test_fraction: float) -> int:
    """Start of the test window: the last `test_fraction` of all de-duplicated check-ins by time
    (counted before back-to-back repeats are collapsed, which is a per-user sequence step)."""
    times = sorted(r.ts_utc for r in rows)
    return times[int(len(times) * (1 - test_fraction))]


def top_categories(totals: dict, venues: dict, k: int = 3) -> list[str]:
    """Categories ranked by the summed score of their candidate venues."""
    by_cat: dict[str, float] = {}
    for v, s in totals.items():
        cat = venues[v][0]
        by_cat[cat] = by_cat.get(cat, 0.0) + s
    return sorted(by_cat, key=lambda c: (-by_cat[c], c))[:k]


def replay(seqs: dict, cutoff: int, cfg: AlgoConfig, popularity: dict, venues: dict) -> dict:
    """Walk every user's sequence; return per-method ranks and per-event details for 'full'."""
    weights = method_weights(cfg)
    pop_order = sorted(venues, key=lambda v: (-popularity.get(v, 0), v))
    pop_pos = {v: i for i, v in enumerate(pop_order)}

    ranks = {m: [] for m in ("popularity", *weights)}
    events = []  # (user, prior_count, hit3_category, hit3_within_1km)
    for user, seq in seqs.items():
        profile = Profile()
        prior = 0
        for venue_id, ts, tz in seq:
            if ts >= cutoff and prior > 0:
                current = profile.current
                # city popularity only: rank among every venue in the city, current excluded
                r = pop_pos[venue_id] + 1
                if current is not None and pop_pos.get(current, math.inf) < pop_pos[venue_id]:
                    r -= 1
                ranks["popularity"].append(r)

                cands, signals, _ = compute_signals(profile, ts, tz, popularity, cfg)
                full_totals = None
                for name, w in weights.items():
                    totals = weighted_totals(cands, signals, w)
                    ranks[name].append(rank_in_totals(totals, venue_id, popularity))
                    if name == "full":
                        full_totals = totals
                top3 = ranked_venues(full_totals, 3, popularity)
                actual = venues[venue_id]
                cat_hit = actual[0] in top_categories(full_totals, venues, 3)
                near_hit = any(haversine_km(actual[1], actual[2], venues[v][1], venues[v][2]) <= 1.0
                               for v in top3)
                events.append((user, prior, cat_hit, near_hit))
            add_checkin(profile, CheckIn(venue_id, ts, tz), cfg)
            prior += 1
    return {"ranks": ranks, "events": events}


def metrics(ranks: list) -> dict:
    n = len(ranks)
    if n == 0:
        return {"n": 0, "top1": 0.0, "top3": 0.0, "top5": 0.0, "mrr": 0.0}

    def hit(k):
        return sum(1 for r in ranks if r is not None and r <= k) / n

    return {
        "n": n,
        "top1": round(hit(1), 4),
        "top3": round(hit(3), 4),
        "top5": round(hit(5), 4),
        "mrr": round(sum(1.0 / r for r in ranks if r) / n, 4),
    }


def extras(full_ranks: list, events: list) -> dict:
    n = len(events)
    hit3 = [r is not None and r <= 3 for r in full_ranks]
    bands = {}
    for label, lo, hi in HISTORY_BANDS:
        idx = [i for i, e in enumerate(events) if lo <= e[1] <= hi]
        bands[label] = {"n": len(idx),
                        "top3": round(sum(hit3[i] for i in idx) / len(idx), 4) if idx else None}
    per_user: dict[str, list[bool]] = {}
    for i, e in enumerate(events):
        per_user.setdefault(e[0], []).append(hit3[i])
    user_rates = [sum(h) / len(h) for h in per_user.values()]
    misses_unseen = sum(1 for r in full_ranks if r is None)
    return {
        "top3_category": round(sum(e[2] for e in events) / n, 4) if n else 0.0,
        "top3_within_1km": round(sum(e[3] for e in events) / n, 4) if n else 0.0,
        "top3_by_history": bands,
        "per_user_median_top3": round(statistics.median(user_rates), 4) if user_rates else 0.0,
        "users_evaluated": len(per_user),
        "never_visited_before": round(misses_unseen / n, 4) if n else 0.0,
        "ceiling_top_any": round(1 - misses_unseen / n, 4) if n else 0.0,
    }


def compare(results: dict) -> list[str]:
    """Lines comparing our figures with the reference run (±1 point)."""
    out = []
    for m, ref in REFERENCE["methods"].items():
        ours = results["methods"][m]
        for key in ("top1", "top3", "top5"):
            diff = (ours[key] - ref[key]) * 100
            if abs(diff) > 1.0:
                out.append(f"{m} {key}: {ours[key]:.1%} vs reference {ref[key]:.1%} ({diff:+.1f} pt)")
    return out


def write_chart(results: dict, path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: skipping chart (pip install -r requirements-dev.txt)")
        return False
    order = ["popularity", "most_visited", "markov", "full_no_time", "full"]
    labels = ["Popularity", "Most visited", "Markov only", "Ours, no time", "Ours (full)"]
    ks = [("top1", "Top-1"), ("top3", "Top-3"), ("top5", "Top-5")]
    colours = ["#9DB4C0", "#5B8FA8", "#0F6E8C"]
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    width = 0.26
    for j, (key, name) in enumerate(ks):
        xs = [i + (j - 1) * width for i in range(len(order))]
        vals = [results["methods"][m][key] * 100 for m in order]
        bars = ax.bar(xs, vals, width, label=name, color=colours[j])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.5, f"{v:.1f}", ha="center", fontsize=7,
                    color="#17212B")
    ax.set_xticks(range(len(order)), labels)
    ax.set_ylabel("Exact venue predicted (%)")
    ax.set_title(f"Next-place accuracy on {results['dataset']['test_checkins']:,} test check-ins (NYC)",
                 fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return True


def run(data_path: Path, settings: dict, cfg: AlgoConfig) -> dict:
    t0 = time.perf_counter()
    raw, _ = load_raw(data_path)
    cleaned = clean(raw)
    seqs = sequences(cleaned, collapse=cfg.collapse_repeats)
    vt = {v.venue_id: (v.category, v.latitude, v.longitude) for v in venue_table(cleaned)}
    cutoff = cutoff_time(cleaned, float(settings["EVAL_TEST_FRACTION"]))
    popularity = Counter(r.venue_id for r in cleaned if r.ts_utc < cutoff)
    t_load = time.perf_counter() - t0

    out = replay(seqs, cutoff, cfg, dict(popularity), vt)
    results = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "algorithm_version": ALGORITHM_VERSION,
        "dataset": {
            "file": data_path.name,
            "rows_raw": len(raw),
            "rows_after_dedup": len(cleaned),
            "rows_after_collapse": sum(len(s) for s in seqs.values()),
            "users": len(seqs),
            "test_fraction": float(settings["EVAL_TEST_FRACTION"]),
            "cutoff_utc": datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "test_checkins": len(out["events"]),
        },
        "config": {
            "MARKOV_ORDER": cfg.markov_order, "HISTORY_LENGTH": cfg.history_length,
            "W_TRANSITION": cfg.w_transition, "W_TIME": cfg.w_time,
            "W_FREQUENCY": cfg.w_frequency, "W_RECENCY": cfg.w_recency,
            "W_POPULARITY": cfg.w_popularity, "RECENT_WINDOW": cfg.recent_window,
            "RECENT_DECAY": cfg.recent_decay, "TIME_BUCKET_HOURS": cfg.time_bucket_hours,
            "COLLAPSE_REPEATS": cfg.collapse_repeats,
        },
        "methods": {m: {"label": METHOD_LABELS[m], **metrics(r)} for m, r in out["ranks"].items()},
        "full_extras": extras(out["ranks"]["full"], out["events"]),
        "reference": REFERENCE,
        "runtime_seconds": {"load": round(t_load, 1), "total": round(time.perf_counter() - t0, 1)},
    }
    results["differences_over_1pt"] = compare(results)
    return results


def print_table(results: dict) -> None:
    d = results["dataset"]
    print(f"Test window from {d['cutoff_utc']} UTC: {d['test_checkins']:,} check-ins "
          f"from {results['full_extras']['users_evaluated']:,} users")
    print()
    print(f"{'Method':<46}{'Top-1':>8}{'Top-3':>8}{'Top-5':>8}{'MRR':>8}   ref top-3")
    for m, r in results["methods"].items():
        ref = REFERENCE["methods"][m]["top3"]
        print(f"{r['label']:<46}{r['top1']:>8.1%}{r['top3']:>8.1%}{r['top5']:>8.1%}{r['mrr']:>8.3f}   {ref:.1%}")
    e = results["full_extras"]
    print()
    print(f"Right category in top 3 cats:   {e['top3_category']:.1%}  (ref 47.4%)")
    print(f"Top-3 place within 1 km:        {e['top3_within_1km']:.1%}  (ref 61.5%)")
    for label, b in e["top3_by_history"].items():
        print(f"Top-3, prior history {label:<7}    {b['top3']:.1%}  (n={b['n']:,})")
    print(f"Per-user median top-3:          {e['per_user_median_top3']:.1%}")
    print(f"Never visited before (ceiling): {e['never_visited_before']:.1%} of test check-ins")
    print(f"Runtime: {results['runtime_seconds']['total']} s")
    if results["differences_over_1pt"]:
        print("\nDifferences > 1 point from the reference run:")
        for line in results["differences_over_1pt"]:
            print("  " + line)
    else:
        print("\nAll top-1/3/5 figures within ±1 point of the reference run.")


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=settings["RAW_DATA_PATH"])
    ap.add_argument("--out", type=Path, default=root / "docs" / "evaluation_results.json")
    ap.add_argument("--chart", type=Path, default=root / "docs" / "accuracy.png")
    ap.add_argument("--no-chart", action="store_true")
    args = ap.parse_args(argv)
    if not args.data.is_file():
        print(f"Dataset not found: {args.data}\nSee README for where to download it.", file=sys.stderr)
        return 2

    cfg = AlgoConfig.from_settings(settings)
    results = run(args.data, settings, cfg)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print_table(results)
    print(f"\nWrote {args.out}")
    if not args.no_chart and write_chart(results, args.chart):
        print(f"Wrote {args.chart}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
