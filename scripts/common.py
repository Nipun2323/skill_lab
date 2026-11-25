"""Reading and cleaning the raw Foursquare NYC file. Shared by import and evaluation."""
from __future__ import annotations

import calendar
import csv
import io
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


@dataclass(frozen=True)
class Row:
    user_id: str
    venue_id: str
    category_id: str
    category: str
    lat: float
    lon: float
    tz_offset_min: int
    ts_utc: int


def parse_utc(text: str) -> int:
    """'Tue Apr 03 18:00:09 +0000 2012' -> epoch seconds (UTC)."""
    parts = text.split()
    if len(parts) != 6:
        raise ValueError(f"bad timestamp: {text!r}")
    _, mon, day, hms, offset, year = parts
    hh, mm, ss = (int(x) for x in hms.split(":"))
    ts = calendar.timegm((int(year), MONTHS[mon], int(day), hh, mm, ss, 0, 0, 0))
    sign = -1 if offset[0] == "-" else 1
    off_sec = sign * (int(offset[1:3]) * 3600 + int(offset[3:5]) * 60)
    return ts - off_sec


def decode(data: bytes) -> tuple[str, str]:
    """The original file is not valid UTF-8 (e.g. 0xE9 in 'Café'): try UTF-8, then latin-1."""
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def load_raw(path: Path) -> tuple[list[Row], dict]:
    """Read the raw file. Detects separator (tab/comma) and an optional header row.
    Columns are read by position. Returns rows (file order) and info about the file."""
    path = Path(path)
    text, encoding = decode(path.read_bytes())
    first_line = text.split("\n", 1)[0]
    sep = "\t" if first_line.count("\t") >= 7 else ","
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    rows: list[Row] = []
    header = False
    for i, f in enumerate(reader):
        if not f:
            continue
        if i == 0 and not _is_number(f[4] if len(f) > 4 else ""):
            header = True
            continue
        if len(f) < 8:
            raise ValueError(f"line {i + 1}: expected 8 columns, got {len(f)}")
        rows.append(Row(f[0].strip(), f[1].strip(), f[2].strip(), f[3].strip(),
                        float(f[4]), float(f[5]), int(float(f[6])), parse_utc(f[7])))
    info = {"encoding": encoding, "separator": "tab" if sep == "\t" else "comma",
            "header": header, "bytes": path.stat().st_size}
    return rows, info


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def clean(rows: list[Row]) -> list[Row]:
    """Drop exact duplicate rows, then sort by user and time (stable on file order)."""
    seen = set()
    unique = []
    for r in rows:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    unique.sort(key=lambda r: (_user_key(r.user_id), r.ts_utc))
    return unique


def _user_key(u: str):
    return (0, int(u), "") if u.isdigit() else (1, 0, u)


def sequences(rows: list[Row], collapse: bool = True) -> dict[str, list[tuple[str, int, int]]]:
    """Per-user sequences (venue_id, ts_utc, tz_offset_min), oldest first.
    Back-to-back check-ins at the same venue count once when collapse is True."""
    out: dict[str, list[tuple[str, int, int]]] = {}
    for r in rows:  # rows are sorted by user, time
        seq = out.setdefault(r.user_id, [])
        if collapse and seq and seq[-1][0] == r.venue_id:
            continue
        seq.append((r.venue_id, r.ts_utc, r.tz_offset_min))
    return out
