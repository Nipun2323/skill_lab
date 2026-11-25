"""Plain data types used by the prediction algorithm."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CheckIn:
    venue_id: str
    ts_utc: int          # seconds since epoch, UTC
    tz_offset_min: int   # local time = UTC + offset


@dataclass(frozen=True)
class AlgoConfig:
    """The fixed design constants the algorithm reads (a view of config.py)."""
    markov_order: int = 1
    history_length: int = 1000
    w_transition: float = 0.40
    w_time: float = 0.30
    w_frequency: float = 0.20
    w_recency: float = 0.10
    w_popularity: float = 0.001
    recent_window: int = 20
    recent_decay: float = 0.8
    time_bucket_hours: int = 3
    collapse_repeats: bool = True
    cold_start_candidates: int = 100

    @classmethod
    def from_settings(cls, s: dict, **override) -> "AlgoConfig":
        values = dict(
            markov_order=int(s["MARKOV_ORDER"]),
            history_length=int(s["HISTORY_LENGTH"]),
            w_transition=float(s["W_TRANSITION"]),
            w_time=float(s["W_TIME"]),
            w_frequency=float(s["W_FREQUENCY"]),
            w_recency=float(s["W_RECENCY"]),
            w_popularity=float(s["W_POPULARITY"]),
            recent_window=int(s["RECENT_WINDOW"]),
            recent_decay=float(s["RECENT_DECAY"]),
            time_bucket_hours=int(s["TIME_BUCKET_HOURS"]),
            collapse_repeats=bool(s["COLLAPSE_REPEATS"]),
            cold_start_candidates=int(s.get("COLD_START_CANDIDATES", 100)),
        )
        values.update(override)
        return cls(**values)

    @property
    def weights(self) -> dict[str, float]:
        return {
            "transition": self.w_transition,
            "time": self.w_time,
            "frequency": self.w_frequency,
            "recency": self.w_recency,
            "popularity": self.w_popularity,
        }


@dataclass
class Profile:
    """Counts summarising one user's history. Built by predictor.algorithm.build_profile."""
    count: dict = field(default_factory=dict)        # venue -> visits
    trans: dict = field(default_factory=dict)        # a -> {b: n}
    trans2: dict = field(default_factory=dict)       # (a, b) -> {c: n}
    at_time: dict = field(default_factory=dict)      # bucket -> {venue: n}
    window: deque = field(default_factory=deque)     # (CheckIn, bucket) kept, oldest first
    recent: list = field(default_factory=list)       # venue ids, newest first

    @property
    def current(self) -> str | None:
        return self.window[-1][0].venue_id if self.window else None

    @property
    def previous(self) -> str | None:
        return self.window[-2][0].venue_id if len(self.window) >= 2 else None

    @property
    def last_ts(self) -> int | None:
        return self.window[-1][0].ts_utc if self.window else None

    def __len__(self) -> int:
        return len(self.window)


@dataclass(frozen=True)
class Score:
    total: float
    parts: dict    # signal name -> normalised signal value (0..1), before weighting
    contrib: dict  # signal name -> weight * value


@dataclass(frozen=True)
class Prediction:
    venue_id: str
    score: float
    probability: float
    reason: str      # reason code: transition | time | frequency | recency | popularity
    signals: dict
