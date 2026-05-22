from __future__ import annotations

import re
from typing import Dict, Mapping, Optional

# Checked in priority order per field — first match wins.
_REMAINING_HEADERS = [
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining",
    "ratelimit-remaining",
]

_LIMIT_HEADERS = [
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit",
    "ratelimit-limit",
]

_RESET_HEADERS = [
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset",
    "ratelimit-reset",
    "retry-after",
]

_NUMERIC_RE = re.compile(r"\A\d+(?:\.\d+)?\Z")
_DURATION_RE = re.compile(r"([\d.]+)(h|m(?!s)|s|ms)")


def extract(headers: Mapping[str, str], now_ms: int) -> Optional[Dict[str, int]]:
    """Extract rate limit quota state from HTTP response headers.

    Returns a dict with rl_remaining, rl_limit, rl_reset_at (all optional),
    or None when no recognised headers are present.
    """
    remaining = _find_integer(headers, _REMAINING_HEADERS)
    limit = _find_integer(headers, _LIMIT_HEADERS)
    reset_at = _find_reset_ms(headers, _RESET_HEADERS, now_ms)

    if remaining is None and limit is None and reset_at is None:
        return None

    result: Dict[str, int] = {}
    if remaining is not None:
        result["rl_remaining"] = remaining
    if limit is not None:
        result["rl_limit"] = limit
    if reset_at is not None:
        result["rl_reset_at"] = reset_at
    return result


def _find_integer(headers: Mapping[str, str], names: list) -> Optional[int]:
    for name in names:
        val = headers.get(name)
        if val is None:
            continue
        try:
            n = int(val.strip())
            if n >= 0:
                return n
        except ValueError:
            continue
    return None


def _find_reset_ms(headers: Mapping[str, str], names: list, now_ms: int) -> Optional[int]:
    for name in names:
        val = headers.get(name)
        if val is None:
            continue
        ms = _normalize_reset_ms(val.strip(), now_ms)
        if ms is not None:
            return ms
    return None


def _normalize_reset_ms(s: str, now_ms: int) -> Optional[int]:
    """Normalise a rate-limit reset value to epoch milliseconds.

    Handles three formats:
      Unix timestamp  — integer > 1_000_000_000  (e.g. "1716000000")
      Seconds-from-now — small integer            (e.g. "30" from Retry-After)
      OpenAI duration  — string like "1s", "20ms", "1m30s", "2h"
    """
    if _NUMERIC_RE.match(s):
        n = float(s)
        if n >= 1_000_000_000:
            return int(n * 1_000)
        return now_ms + int(n * 1_000)

    ms = _parse_duration_ms(s)
    return (now_ms + ms) if ms is not None else None


def _parse_duration_ms(s: str) -> Optional[int]:
    """Parse an OpenAI-style duration string to milliseconds.

    Examples: "1s" → 1000, "20ms" → 20, "1m30s" → 90000, "2h" → 7200000
    """
    total = 0
    found = False
    for val, unit in _DURATION_RE.findall(s):
        found = True
        v = float(val)
        if unit == "h":
            total += int(v * 3_600_000)
        elif unit == "m":
            total += int(v * 60_000)
        elif unit == "s":
            total += int(v * 1_000)
        elif unit == "ms":
            total += int(v)
    if found and total > 0:
        return total
    return None
