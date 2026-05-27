"""Rate-limit quota extraction from HTTP response headers.

Normalises three distinct header families into three canonical event fields:

``rl_remaining``
    Requests remaining in the current window (integer ≥ 0).

``rl_limit``
    Total quota for the window (integer ≥ 0).

``rl_reset_at``
    When the window resets, expressed as epoch **milliseconds** (integer).

WHY in the SDK rather than the collector?
Response headers are only visible at the HTTP call site.  By the time an
event reaches the collector only the status code and duration are known;
header data would have to be forwarded verbatim, adding payload size for
every request.  Extracting and normalising here keeps the event schema
stable and the payload small.

Returns ``None`` when none of the recognised headers are present so the
caller can omit the ``rl_*`` fields entirely rather than sending nulls.

Header priority order (first match per field wins):

Remaining
  1. ``x-ratelimit-remaining-requests``  (OpenAI / Anthropic)
  2. ``x-ratelimit-remaining``           (GitHub)
  3. ``ratelimit-remaining``             (IETF draft, HubSpot, Fastly)

Limit
  1. ``x-ratelimit-limit-requests``      (OpenAI / Anthropic)
  2. ``x-ratelimit-limit``               (GitHub)
  3. ``ratelimit-limit``                 (IETF draft)

Reset
  1. ``x-ratelimit-reset-requests``      (OpenAI — duration string format)
  2. ``x-ratelimit-reset``               (GitHub — Unix timestamp seconds)
  3. ``ratelimit-reset``                 (IETF draft)
  4. ``retry-after``                     (Stripe / generic 429 fallback)
"""

from __future__ import annotations

import re
from typing import Dict, List, Mapping, Optional

# ---------------------------------------------------------------------------
# Header name priority lists
# ---------------------------------------------------------------------------

_REMAINING_HEADERS: List[str] = [
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining",
    "ratelimit-remaining",
]

_LIMIT_HEADERS: List[str] = [
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit",
    "ratelimit-limit",
]

_RESET_HEADERS: List[str] = [
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset",
    "ratelimit-reset",
    "retry-after",
]

# Matches a bare non-negative number, optionally with a decimal component.
# Used to distinguish numeric values ("30", "1716000000") from duration
# strings ("1s", "1m30s").
_NUMERIC_RE = re.compile(r"\A\d+(?:\.\d+)?\Z")

# Captures one component of an OpenAI-style duration string.
# Groups: (value, unit) where unit is h | m (not ms) | s | ms.
# The negative lookahead (?!s) distinguishes "m" (minute) from "ms".
_DURATION_RE = re.compile(r"([\d.]+)(h|m(?!s)|s|ms)")


def extract(headers: Mapping[str, str], now_ms: int) -> Optional[Dict[str, int]]:
    """Extract rate-limit quota fields from an HTTP response header mapping.

    Args:
        headers: Response headers, typically ``response.headers`` from
            *requests* or *httpx*.  Header names are matched
            case-insensitively when the mapping supports it (both libraries
            return case-insensitive dicts by default).
        now_ms: Current epoch time in milliseconds.  Used as the reference
            point when converting relative reset values (seconds-from-now,
            duration strings) to absolute epoch milliseconds.

    Returns:
        A dict containing any combination of ``rl_remaining`` (int),
        ``rl_limit`` (int), and ``rl_reset_at`` (int epoch ms), or
        ``None`` if none of the recognised headers are present.  Fields
        with no matching header are omitted rather than set to ``None``.
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


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _find_integer(headers: Mapping[str, str], names: List[str]) -> Optional[int]:
    """Return the first non-negative integer found across *names* in *headers*.

    Args:
        headers: Response header mapping.
        names: Header names to check, in priority order.

    Returns:
        The parsed integer value, or ``None`` if no header matches or all
        matched values fail to parse as a non-negative integer.
    """
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


def _find_reset_ms(headers: Mapping[str, str], names: List[str], now_ms: int) -> Optional[int]:
    """Return the first parseable reset time found across *names* as epoch ms.

    Args:
        headers: Response header mapping.
        names: Header names to check, in priority order.
        now_ms: Current epoch time in milliseconds, used as the reference
            point for relative reset values.

    Returns:
        Epoch milliseconds for the reset time, or ``None`` if no header
        matches or no value can be normalised.
    """
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

    Unix timestamp
        A large integer (> 1 000 000 000) interpreted as seconds since the
        Unix epoch, e.g. ``"1716000000"`` → multiplied by 1 000.

    Seconds-from-now
        A small non-negative integer, e.g. ``"30"`` from a ``Retry-After``
        header → *now_ms* + 30 000.

    OpenAI duration string
        A compound string like ``"1s"``, ``"20ms"``, ``"1m30s"``, ``"2h"``
        → parsed by :func:`_parse_duration_ms` then added to *now_ms*.

    Args:
        s: Stripped header value string.
        now_ms: Current epoch time in milliseconds.

    Returns:
        Epoch milliseconds, or ``None`` if the value cannot be parsed in
        any of the three formats.
    """
    if _NUMERIC_RE.match(s):
        n = float(s)
        if n >= 1_000_000_000:
            return int(n * 1_000)
        return now_ms + int(n * 1_000)

    ms = _parse_duration_ms(s)
    return (now_ms + ms) if ms is not None else None


def _parse_duration_ms(s: str) -> Optional[int]:
    """Parse an OpenAI-style duration string into milliseconds.

    Recognises the following units (combinable, e.g. ``"1m30s"``):

    =====  =============================
    Unit   Meaning
    =====  =============================
    ``h``  hours
    ``m``  minutes (not ``ms``)
    ``s``  seconds
    ``ms`` milliseconds
    =====  =============================

    Args:
        s: Duration string, e.g. ``"1s"``, ``"20ms"``, ``"1m30s"``, ``"2h"``.

    Returns:
        Total milliseconds as an integer (including ``0`` for ``"0ms"``), or
        ``None`` if the string contains no recognised unit tokens.

    Examples::

        _parse_duration_ms("1s")     # → 1000
        _parse_duration_ms("20ms")   # → 20
        _parse_duration_ms("1m30s")  # → 90000
        _parse_duration_ms("2h")     # → 7200000
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
    if found:
        return total
    return None
