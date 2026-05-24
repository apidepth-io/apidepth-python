"""Lightweight event schema for the Apidepth SDK.

WHY validate here rather than at the collector?
An event missing ``duration_ms`` or ``vendor`` is garbage data.  If we let
it reach the collector it gets ingested, pollutes time-series, and surfaces
only when a customer asks why their p95 chart looks wrong.  Raising
``ValueError`` at ``build()`` time means the bug surfaces in tests and
development, never in production data.

WHY return a plain dict rather than a dataclass?
``json.dumps`` works directly on a dict.  A dataclass requires
``.to_dict()`` before serialisation, adding a conversion step on every
batch.  The dict gives us serialisation transparency without extra overhead.
"""

from __future__ import annotations

from typing import Any, Dict

#: Fields that must be present on every event regardless of outcome.
#: ``error_class`` and rate-limit fields (``rl_*``) are optional.
REQUIRED: frozenset = frozenset({"vendor", "endpoint", "method", "outcome", "duration_ms", "ts"})


def build(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Validate *attrs* and return a shallow-copied event dict.

    Args:
        attrs: Mapping of event field names to values.  Must contain all
            keys listed in :data:`REQUIRED`.  Optional fields (``status``,
            ``cold_start``, ``env``, ``error_class``, ``rl_remaining``,
            ``rl_limit``, ``rl_reset_at``) are passed through unchanged.

    Returns:
        A new dict with the same contents as *attrs*.  Callers must not
        mutate the returned dict; treat it as logically immutable.

    Raises:
        ValueError: If any key from :data:`REQUIRED` is absent.  The
            message names every missing field so the bug is easy to locate.

    Example::

        event = build({
            "vendor": "stripe",
            "endpoint": "/v1/charges/:id",
            "method": "POST",
            "outcome": "success",
            "duration_ms": 234,
            "ts": 1747008000000,
        })
    """
    missing = REQUIRED - attrs.keys()
    if missing:
        raise ValueError(
            f"Apidepth event is missing required fields: {', '.join(sorted(missing))}. "
            "This is a bug in the SDK — please open an issue."
        )
    return dict(attrs)
