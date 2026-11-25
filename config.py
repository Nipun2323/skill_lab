"""SyncroMove settings.

Defaults live here. Any setting can be overridden by an environment variable of the
same name, or by a line `NAME=value` in a `.env` file next to this one (env vars win).

The algorithm weights are fixed design constants, not fitted values. Changing one is a
config change that must be re-measured with `python scripts/evaluate.py` and reported.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DEFAULTS: dict[str, object] = {
    # Data files
    "RAW_DATA_PATH": "data/raw/dataset_TSMC2014_NYC.txt",
    "VENUES_PATH": "data/venues.csv.gz",
    "SAMPLES_PATH": "data/samples/sample_histories.json.gz",
    "DATABASE_PATH": "instance/syncromove.db",
    "EVAL_RESULTS_PATH": "docs/evaluation_results.json",
    "SECRET_KEY": "",
    # Algorithm (fixed, explainable; no training)
    "MARKOV_ORDER": 1,
    "HISTORY_LENGTH": 1000,
    "W_TRANSITION": 0.40,
    "W_TIME": 0.30,
    "W_FREQUENCY": 0.20,
    "W_RECENCY": 0.10,
    "W_POPULARITY": 0.001,
    "RECENT_WINDOW": 20,
    "RECENT_DECAY": 0.8,
    "TIME_BUCKET_HOURS": 3,
    "COLLAPSE_REPEATS": True,
    "COLD_START_CANDIDATES": 100,
    # Evaluation / import
    "EVAL_TEST_FRACTION": 0.2,
    "SAMPLE_USERS": 25,
    "RANDOM_SEED": 42,
    # App
    "TOP_K": 3,
    "MAX_K": 10,
    "PASSWORD_MIN": 8,
    "LOGIN_MAX_ATTEMPTS": 5,
    "LOGIN_WINDOW_MINUTES": 15,
    "SESSION_HOURS": 12,
    "HOST": "127.0.0.1",
    "PORT": 5000,
}

PATH_KEYS = ("RAW_DATA_PATH", "VENUES_PATH", "SAMPLES_PATH", "DATABASE_PATH", "EVAL_RESULTS_PATH")

ALGORITHM_VERSION = "rule-v1"


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _cast(default: object, raw: str) -> object:
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


def load_settings(overrides: dict | None = None, use_env: bool = True) -> dict:
    """Defaults <- .env <- environment <- overrides. Relative paths resolve against BASE_DIR."""
    settings = dict(DEFAULTS)
    if use_env:
        sources = [_read_dotenv(BASE_DIR / ".env"), os.environ]
        for source in sources:
            for key, default in DEFAULTS.items():
                if key in source and source[key] != "":
                    settings[key] = _cast(default, source[key])
    if overrides:
        settings.update(overrides)
    for key in PATH_KEYS:
        path = Path(settings[key])
        settings[key] = path if path.is_absolute() else BASE_DIR / path
    return settings
