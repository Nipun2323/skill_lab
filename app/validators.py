"""Every server-side validation rule. Raise ValidationError; the API turns it into
{"error": {"code", "field", "message"}} with the given HTTP status."""
from __future__ import annotations

import re
from datetime import datetime

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")
VENUE_RE = re.compile(r"^[0-9a-f]{24}$")
FUTURE_TOLERANCE_S = 5 * 60


class ValidationError(Exception):
    def __init__(self, field: str | None, message: str, status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.field = field
        self.message = message
        self.status = status
        self.code = code

    def as_dict(self) -> dict:
        return {"error": {"code": self.code, "field": self.field, "message": self.message}}


def email(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("email", "Enter your email address.", code="required")
    value = value.strip()
    if len(value) > 254:
        raise ValidationError("email", "Email must be 254 characters or fewer.")
    if not EMAIL_RE.match(value):
        raise ValidationError("email", "Enter a valid email address, like name@example.com.")
    return value


def password(pw, confirm=None, minimum: int = 8, check_confirm: bool = True) -> str:
    if not isinstance(pw, str) or not pw:
        raise ValidationError("password", "Enter a password.", code="required")
    if len(pw) < minimum:
        raise ValidationError("password", f"Password needs at least {minimum} characters.")
    if len(pw) > 128:
        raise ValidationError("password", "Password must be 128 characters or fewer.")
    if not re.search(r"[A-Za-z]", pw):
        raise ValidationError("password", "Password needs at least one letter.")
    if not re.search(r"[0-9]", pw):
        raise ValidationError("password", "Password needs at least one number.")
    if check_confirm and confirm != pw:
        raise ValidationError("confirm", "Passwords don't match.")
    return pw


def parse_iso(value, field: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(field, "Enter a date and time.", code="required")
    text = value.strip()
    if len(text) > 40:
        raise ValidationError(field, "Use an ISO 8601 date and time, like 2024-05-01T18:30:00+05:30.")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00") if text.endswith("Z") else text)
    except ValueError:
        raise ValidationError(field, "Use an ISO 8601 date and time, like 2024-05-01T18:30:00+05:30.")
    if dt.tzinfo is None:
        raise ValidationError(field, "Include a timezone offset, like +05:30 or Z.")
    return dt


def timestamp(value, now: int, last_ts: int | None) -> int:
    """Check-in time -> epoch seconds. None means now. Not >5 min in the future, not before
    the user's last check-in."""
    if value is None:
        ts = now
    else:
        ts = int(parse_iso(value).timestamp())
        if ts > now + FUTURE_TOLERANCE_S:
            raise ValidationError("timestamp", "Check-in time can't be in the future.")
    if last_ts is not None and ts < last_ts:
        raise ValidationError("timestamp", "Check-in time can't be earlier than your last check-in.",
                              status=409, code="out_of_order")
    return ts


def _int(value, field: str, label: str) -> int:
    if isinstance(value, bool):
        raise ValidationError(field, f"{label} must be a whole number.")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,6}", value.strip()):
        return int(value.strip())
    raise ValidationError(field, f"{label} must be a whole number.")


def tz_offset(value, field: str = "tz_offset_min") -> int:
    if value is None or value == "":
        raise ValidationError(field, "Timezone offset is required.", code="required")
    n = _int(value, field, "Timezone offset")
    if not -720 <= n <= 840:
        raise ValidationError(field, "Timezone offset must be between -720 and 840 minutes.")
    return n


def k_value(value, max_k: int = 10, default: int = 3) -> int:
    if value is None or value == "":
        return default
    n = _int(value, "k", "k")
    if not 1 <= n <= max_k:
        raise ValidationError("k", f"k must be between 1 and {max_k}.")
    return n


def limit(value, low: int, high: int, default: int, field: str = "limit") -> int:
    if value is None or value == "":
        return default
    n = _int(value, field, "limit")
    if not low <= n <= high:
        raise ValidationError(field, f"limit must be between {low} and {high}.")
    return n


def venue_id(value) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError("venue_id", "Choose a place from the search results.", code="required")
    if not VENUE_RE.match(value):
        raise ValidationError("venue_id", "That place id isn't valid. Choose a place from the search results.")
    return value


def search_query(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("q", "Type a kind of place to search for, like coffee.", code="required")
    value = value.strip()
    if len(value) > 60:
        raise ValidationError("q", "Search text must be 60 characters or fewer.")
    return value


def json_object(body) -> dict:
    if not isinstance(body, dict):
        raise ValidationError(None, "Request body must be a JSON object.")
    return body
