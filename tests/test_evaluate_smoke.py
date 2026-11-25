import json
import random
import time
from datetime import datetime, timedelta, timezone

from scripts import evaluate


def make_raw(path, rows=5000, users=40, venues=300, seed=7):
    rnd = random.Random(seed)
    cats = ["Bar", "Office", "Café", "Gym", "Park", "Subway"]  # includes a non-ASCII name
    vids = [f"{i:024x}" for i in range(venues)]
    start = datetime(2012, 4, 3, 18, tzinfo=timezone.utc)
    lines = []
    for i in range(rows):
        u = rnd.randrange(users) + 1
        v = rnd.randrange(venues)
        t = start + timedelta(minutes=97 * i)
        lines.append("\t".join([str(u), vids[v], f"cat{v % 6}", cats[v % 6], f"{40.7 + v / 1000:.6f}",
                                f"{-74 + v / 1000:.6f}", "-240", t.strftime("%a %b %d %H:%M:%S +0000 %Y")]))
    path.write_bytes(("\n".join(lines) + "\n").encode("latin-1"))


def test_evaluate_runs_quickly(tmp_path):
    raw = tmp_path / "raw.txt"
    make_raw(raw)
    out = tmp_path / "res.json"
    t0 = time.perf_counter()
    assert evaluate.main(["--data", str(raw), "--out", str(out), "--no-chart"]) == 0
    assert time.perf_counter() - t0 < 30
    res = json.loads(out.read_text())
    assert res["dataset"]["test_checkins"] > 0
    for m in ("popularity", "most_visited", "markov", "full_no_time", "full"):
        r = res["methods"][m]
        assert 0 <= r["top1"] <= r["top3"] <= r["top5"] <= 1


def test_import_reads_csv_with_header(tmp_path):
    from scripts.common import load_raw
    p = tmp_path / "k.csv"
    p.write_text("userId,venueId,venueCategoryId,venueCategory,latitude,longitude,timezoneOffset,utcTimestamp\n"
                 '1,' + "a" * 24 + ',c1,"Bar, Pub",40.7,-74.0,-240,Tue Apr 03 18:00:09 +0000 2012\n',
                 encoding="utf-8")
    rows, info = load_raw(p)
    assert info["header"] and info["separator"] == "comma"
    assert rows[0].category == "Bar, Pub" and rows[0].tz_offset_min == -240
