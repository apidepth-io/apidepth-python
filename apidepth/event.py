from __future__ import annotations

from typing import Any, Dict

REQUIRED = frozenset({"vendor", "endpoint", "method", "outcome", "duration_ms", "ts"})


def build(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and return an immutable event dict.

    Raises ValueError immediately on missing required fields so the bug
    surfaces at call site rather than silently polluting the collector.
    """
    missing = REQUIRED - attrs.keys()
    if missing:
        raise ValueError(
            f"Apidepth event is missing required fields: {', '.join(sorted(missing))}. "
            "This is a bug in the SDK — please open an issue."
        )
    # Return a shallow copy; callers should not mutate after build.
    return dict(attrs)
