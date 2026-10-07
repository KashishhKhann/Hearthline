"""Input validation, phone normalisation and rate limiting for the public API."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from store import PROJECT_ROOT, now_iso

DATASET_PATH = PROJECT_ROOT / "data" / "proptech-test-data.json"

MAX_BODY_BYTES = 20_000
ALLOWED_FIELDS = {
    "name": 120, "email": 200, "phone": 40, "unit": 40, "property_name": 120, "issue": 5_000,
}
REQUIRED_FIELDS = ("name", "unit", "property_name", "issue")
HONEYPOT_FIELD = "website"  # hidden in the form; humans leave it empty, bots fill it

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def normalize_phone(raw: str, default_country_code: str | None = None) -> str | None:
    """Return an E.164 number (+3538...) or None if it can't be a valid phone number.

    Irish numbers written locally (087 123 4567) get the default country code.
    """
    if not raw:
        return None
    cc = (default_country_code or os.getenv("DEFAULT_COUNTRY_CODE", "353")).lstrip("+")
    text = raw.strip()
    digits = re.sub(r"[^\d+]", "", text)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    elif digits.startswith("0"):
        digits = f"+{cc}{digits[1:]}"
    elif not digits.startswith("+"):
        digits = f"+{cc}{digits}"
    if not re.fullmatch(r"\+\d{8,15}", digits):
        return None
    return digits


def known_properties(dataset_path: Path = DATASET_PATH) -> list[str]:
    """Building names residents can pick from ([] if the dataset is missing)."""
    try:
        raw = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
        props = (raw.get("metadata") or {}).get("properties") or []
    except (OSError, ValueError, AttributeError):
        return []
    names = [str(p.get("name", "")).strip() for p in props if isinstance(p, dict)]
    return sorted({n for n in names if n}, key=str.lower)


def clean_report(payload: object, allowed_properties: list[str] | None = None) -> dict:
    """Validate and normalise a portal submission. Raises ValueError on bad input."""
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object.")
    data = {
        key: str(payload.get(key, "") or "").strip()[:limit]
        for key, limit in ALLOWED_FIELDS.items()
    }
    missing = [key for key in REQUIRED_FIELDS if not data[key]]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    if allowed_properties:
        lookup = {name.lower(): name for name in allowed_properties}
        canonical = lookup.get(data["property_name"].lower())
        if canonical is None:
            raise ValueError("Unknown property. Please choose one from the list.")
        data["property_name"] = canonical
    if data["email"] and not _EMAIL_RE.match(data["email"]):
        raise ValueError("That email address doesn't look right.")
    if data["phone"]:
        phone = normalize_phone(data["phone"])
        if phone is None:
            raise ValueError("That phone number doesn't look right.")
        data["phone"] = phone
    data["sms_consent"] = bool(payload.get("sms_consent")) and bool(data["phone"])
    data["id"] = str(uuid.uuid4())
    data["timestamp"] = now_iso()
    data["source"] = "resident_portal"
    data["status"] = "new"
    return data


class RateLimiter:
    """Sliding-window limiter keyed by client IP (in-memory, per process)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: float, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= window_s:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
