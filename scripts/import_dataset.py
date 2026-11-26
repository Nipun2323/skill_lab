"""Import the Foursquare NYC check-in file.

Writes
  data/venues.csv.gz                       venue_id,category,latitude,longitude,popularity
  data/samples/sample_histories.json.gz    {dataset_user_id: [[venue_id, ts_utc, tz_offset_min], ...]}
and prints the dataset statistics.

Usage:  python scripts/import_dataset.py [--data PATH] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import random
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_settings  # noqa: E402
from scripts.common import Row, clean, load_raw, sequences  # noqa: E402

# Facts from the project spec (§4.1), checked after import.
EXPECTED = {
    "raw_rows": 227_428,
    "rows_after_dedup": 227_178,
    "users": 1_083,
    "venues": 38_333,
    "category_names": 251,
    "category_ids": 400,
    "min_checkins_per_user": 100,
    "median_checkins_per_user": 153,
    "max_checkins_per_user": 2_697,
    "first_utc": "2012-04-03 18:00",
    "last_utc": "2013-02-16 02:35",
}

# Demo histories: 100–500 check-ins, at least 10 different places, spread over at least 9 months
# (the whole file covers 10.5 months), so a sample shifted to "the past year" fills most of it.
SAMPLE_MIN, SAMPLE_MAX = 100, 500
SAMPLE_MIN_PLACES = 10
SAMPLE_MIN_SPAN_DAYS = 270


@dataclass(frozen=True)
class Venue:
    venue_id: str
    category: str
    latitude: float
    longitude: float
    popularity: int


def venue_table(rows: list[Row]) -> list[Venue]:
    """Median coordinates, most frequent category label, popularity = check-ins."""
    lats: dict[str, list[float]] = {}
    lons: dict[str, list[float]] = {}
    cats: dict[str, Counter] = {}
    for r in rows:
        lats.setdefault(r.venue_id, []).append(r.lat)
        lons.setdefault(r.venue_id, []).append(r.lon)
        cats.setdefault(r.venue_id, Counter())[r.category] += 1
    out = []
    for v in sorted(lats):
        out.append(Venue(v, cats[v].most_common(1)[0][0], round(statistics.median(lats[v]), 6),
                         round(statistics.median(lons[v]), 6), len(lats[v])))
    return out


def write_venues(venues: list[Venue], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["venue_id", "category", "latitude", "longitude", "popularity"])
    for v in venues:
        w.writerow([v.venue_id, v.category, f"{v.latitude:.6f}", f"{v.longitude:.6f}", v.popularity])
    # mtime=0 keeps the gzip bytes identical between runs
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))


def pick_samples(seqs: dict, n: int, seed: int) -> dict:
    def suitable(s) -> bool:
        return (SAMPLE_MIN <= len(s) <= SAMPLE_MAX
                and len({venue for venue, _, _ in s}) >= SAMPLE_MIN_PLACES
                and s[-1][1] - s[0][1] >= SAMPLE_MIN_SPAN_DAYS * 86400)

    eligible = sorted((u for u, s in seqs.items() if suitable(s)), key=lambda u: (len(u), u))
    chosen = sorted(random.Random(seed).sample(eligible, min(n, len(eligible))),
                    key=lambda u: (len(u), u))
    return {u: [list(x) for x in seqs[u]] for u in chosen}


def write_samples(samples: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(samples, separators=(",", ":")).encode("utf-8")
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(data)


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def compute_stats(raw: list[Row], cleaned: list[Row], seqs: dict, venues: list[Venue]) -> dict:
    # Per-user figures are counted on the raw rows, as the dataset description does;
    # the max after dropping duplicates is reported separately.
    per_user = Counter(r.user_id for r in raw)
    counts = sorted(per_user.values())
    deduped_max = max(Counter(r.user_id for r in cleaned).values())
    ts = [r.ts_utc for r in cleaned]
    return {
        "raw_rows": len(raw),
        "rows_after_dedup": len(cleaned),
        "duplicates_dropped": len(raw) - len(cleaned),
        "users": len(per_user),
        "venues": len(venues),
        "category_names": len({r.category for r in cleaned}),
        "category_ids": len({r.category_id for r in cleaned}),
        "min_checkins_per_user": counts[0],
        "median_checkins_per_user": statistics.median(counts),
        "max_checkins_per_user": counts[-1],
        "max_per_user_after_dedup": deduped_max,
        "first_utc": _fmt_ts(min(ts)),
        "last_utc": _fmt_ts(max(ts)),
        "rows_after_collapse": sum(len(s) for s in seqs.values()),
    }


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=settings["RAW_DATA_PATH"])
    ap.add_argument("--venues-out", type=Path, default=settings["VENUES_PATH"])
    ap.add_argument("--samples-out", type=Path, default=settings["SAMPLES_PATH"])
    ap.add_argument("--sample-users", type=int, default=settings["SAMPLE_USERS"])
    ap.add_argument("--seed", type=int, default=settings["RANDOM_SEED"])
    ap.add_argument("--no-check", action="store_true", help="skip comparison with the spec's stats")
    args = ap.parse_args(argv)

    if not args.data.is_file():
        print(f"Dataset not found: {args.data}\nDownload 'dataset_TSMC2014_NYC.txt' (see README) "
              f"and pass --data PATH.", file=sys.stderr)
        return 2

    t0 = time.perf_counter()
    raw, info = load_raw(args.data)
    cleaned = clean(raw)
    seqs = sequences(cleaned, collapse=bool(settings["COLLAPSE_REPEATS"]))
    venues = venue_table(cleaned)
    samples = pick_samples(seqs, args.sample_users, args.seed)
    write_venues(venues, args.venues_out)
    write_samples(samples, args.samples_out)
    stats = compute_stats(raw, cleaned, seqs, venues)

    print(f"Read {args.data.name}: {info['bytes'] / 1e6:.1f} MB, {info['encoding']}, "
          f"{info['separator']}-separated, header={info['header']}")
    print()
    print(f"{'statistic':<28}{'measured':>20}{'spec §4.1':>20}  ")
    mismatches = 0
    for key, value in stats.items():
        expected = EXPECTED.get(key)
        mark = ""
        if expected is not None and not args.no_check:
            ok = str(value) == str(expected) or (isinstance(value, (int, float)) and value == expected)
            mark = "ok" if ok else "MISMATCH"
            mismatches += 0 if ok else 1
        shown_exp = "" if expected is None else (f"{expected:,}" if isinstance(expected, int) else expected)
        shown_val = f"{value:,}" if isinstance(value, int) else str(value)
        print(f"{key:<28}{shown_val:>20}{shown_exp:>20}  {mark}")
    print()
    print(f"Wrote {args.venues_out} ({len(venues):,} venues, {args.venues_out.stat().st_size / 1e6:.2f} MB)")
    print(f"Wrote {args.samples_out} ({len(samples)} sample histories, "
          f"{sum(len(s) for s in samples.values()):,} check-ins)")
    print(f"Done in {time.perf_counter() - t0:.1f} s")
    if mismatches:
        print(f"\n{mismatches} statistic(s) differ from the spec.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
