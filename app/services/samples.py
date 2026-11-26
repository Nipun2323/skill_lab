"""Sample New York histories, moved into the past year when they are copied to a new account."""
from __future__ import annotations

import gzip
import json
import secrets
import time
from pathlib import Path

from flask import current_app

WEEK = 7 * 24 * 3600


def sample_histories() -> dict:
    cache = current_app.extensions.get("syncromove_samples")
    if cache is None:
        path = Path(current_app.config["SAMPLES_PATH"])
        cache = json.loads(gzip.decompress(path.read_bytes())) if path.is_file() else {}
        current_app.extensions["syncromove_samples"] = cache
    return cache


def shift_to_recent(rows: list, now: int) -> list:
    """Move a sample history forward by a whole number of weeks so its last check-in falls in
    the 7 days before `now`. Whole weeks keep every visit's weekday and local time of day, so the
    time-of-day signal still works. Samples cover at most 358 days, so the shifted history stays
    inside the past year."""
    last = max(int(r[1]) for r in rows)
    shift = ((now - last) // WEEK) * WEEK  # floor: a history ending after `now` moves back
    return [[r[0], int(r[1]) + shift, int(r[2])] for r in rows]


def pick_sample(now: int | None = None) -> tuple[str | None, list]:
    """(dataset user id, rows moved into the past year), or (None, []) if no samples are loaded."""
    samples = sample_histories()
    if not samples:
        return None, []
    key = secrets.choice(sorted(samples))
    return key, shift_to_recent(samples[key], int(now if now is not None else time.time()))
