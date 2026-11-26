"""Recording a check-in: every server-side rule in one place, used by the JSON API and the
Streamlit front end."""
from __future__ import annotations

import time

from .. import validators as v
from . import prediction_service as ps
from . import repository as repo


def record_checkin(user_id: int, venue_id, timestamp, tz_offset_min) -> dict:
    """Validate and store one check-in. Raises validators.ValidationError on any broken rule.
    `timestamp` is an ISO 8601 string with offset, or None for now."""
    venue_id = v.venue_id(venue_id)
    tz = v.tz_offset(tz_offset_min)
    venue = ps.venue_dict(venue_id)
    if venue is None:
        raise v.ValidationError("venue_id", "We couldn't find that place. Search again and pick one from the list.",
                                status=404, code="not_found")
    last = repo.last_checkin(user_id)
    if last is not None and last["venue_id"] == venue_id:
        raise v.ValidationError("venue_id", "You're already checked in here.", status=409, code="duplicate")
    ts = v.timestamp(timestamp, now=int(time.time()), last_ts=last["ts_utc"] if last else None)
    rank = repo.rank_in_last_prediction(user_id, last["checkin_id"] if last else None, venue_id)
    checkin_id = repo.add_checkin(user_id, venue_id, ts, tz, "app", rank)
    return {"checkin_id": checkin_id, "venue_id": venue_id, "category": venue["category"],
            "ts_utc": ts, "predicted_rank": rank}
