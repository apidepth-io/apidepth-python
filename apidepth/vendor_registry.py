"""Vendor registry: maps hostnames to vendor slugs and normalises API paths.

At import time the registry is seeded from :data:`BUNDLED_BASELINE` — a
hard-coded snapshot of every vendor the SDK ships with.  The
:class:`VendorRegistry` class then holds the live (possibly remote-refreshed)
copy as class-level state protected by a single ``threading.Lock``.

Path normalisation happens in two stages:

1. **Vendor-specific patterns** — compiled from the registry's ``patterns``
   list.  Each pattern is a ``(match, replace)`` pair applied with
   ``re.sub``; the first matching pattern wins.

2. **Generic fallbacks** — applied unconditionally after vendor patterns.
   They strip UUIDs, long numeric IDs, and opaque hex tokens so paths like
   ``/users/12345`` become ``/users/:id`` even for custom/unknown vendors.

Query strings are stripped before either stage so ``?page=2`` never leaks
into normalised endpoints.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Dict, List, Optional, Tuple

_logger = logging.getLogger("apidepth")

#: Hard-coded registry shipped with the SDK.  Used on cold-start before the
#: remote registry can be fetched and as the last-resort fallback if both
#: the network and the disk cache are unavailable.
BUNDLED_BASELINE: dict = {
    "version": "bundled",
    "vendors": {
        "stripe": {
            "hosts": ["api.stripe.com"],
            "patterns": [
                {"match": r"/v1/charges/ch_\w+", "replace": "/v1/charges/:id"},
                {"match": r"/v1/customers/cus_\w+", "replace": "/v1/customers/:id"},
                {"match": r"/v1/payment_intents/pi_\w+", "replace": "/v1/payment_intents/:id"},
                {"match": r"/v1/subscriptions/sub_\w+", "replace": "/v1/subscriptions/:id"},
                {"match": r"/v1/invoices/in_\w+", "replace": "/v1/invoices/:id"},
                {"match": r"/v1/refunds/re_\w+", "replace": "/v1/refunds/:id"},
            ],
        },
        "openai": {
            "hosts": ["api.openai.com"],
            "patterns": [
                {"match": r"/v1/chat/completions", "replace": "/v1/chat/completions"},
                {"match": r"/v1/embeddings", "replace": "/v1/embeddings"},
                {"match": r"/v1/images/generations", "replace": "/v1/images/generations"},
                {"match": r"/v1/files/file-\w+", "replace": "/v1/files/:id"},
            ],
        },
        "anthropic": {
            "hosts": ["api.anthropic.com"],
            "patterns": [
                {"match": r"/v1/messages", "replace": "/v1/messages"},
            ],
        },
        "twilio": {
            "hosts": ["api.twilio.com"],
            "patterns": [
                {
                    "match": r"/2010-04-01/Accounts/AC\w+/Messages/SM\w+",
                    "replace": "/Accounts/:id/Messages/:id",
                },
                {
                    "match": r"/2010-04-01/Accounts/AC\w+/Messages",
                    "replace": "/Accounts/:id/Messages",
                },
                {
                    "match": r"/2010-04-01/Accounts/AC\w+/Calls/CA\w+",
                    "replace": "/Accounts/:id/Calls/:id",
                },
                {"match": r"/2010-04-01/Accounts/AC\w+/Calls", "replace": "/Accounts/:id/Calls"},
            ],
        },
        "resend": {
            "hosts": ["api.resend.com"],
            "patterns": [
                {"match": r"/emails/[0-9a-f-]{36}", "replace": "/emails/:id"},
            ],
        },
        "github": {
            "hosts": ["api.github.com"],
            "patterns": [
                {
                    "match": r"/repos/[^/]+/[^/]+/pulls/\d+",
                    "replace": "/repos/:owner/:repo/pulls/:number",
                },
                {
                    "match": r"/repos/[^/]+/[^/]+/issues/\d+",
                    "replace": "/repos/:owner/:repo/issues/:number",
                },
                {"match": r"/repos/[^/]+/[^/]+", "replace": "/repos/:owner/:repo"},
                {"match": r"/users/[^/]+", "replace": "/users/:username"},
            ],
        },
    },
}

#: Generic path-normalisation rules applied to every request after
#: vendor-specific patterns.  All three are applied in order so a path
#: like ``/v1/orgs/abc123def456ghi789jkl012`` first has the UUID pattern
#: checked (no match), then the numeric pattern (no match), then the token
#: pattern (matches — replaced with ``/:token``).
_GENERIC_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "/:uuid"),
    (re.compile(r"/\d{4,}"), "/:id"),
    (re.compile(r"/[a-f0-9]{24,}", re.IGNORECASE), "/:token"),
]

# Constructs that can enable arbitrary code execution inside Python's re
# engine or create pathological backtracking.  Legitimate path-normalisation
# patterns never need these; their presence in a registry update is a signal
# of either a bug or a compromise.
#
# Blocked:
#   (?{   — code-execution construct
#   (?#   — inline comment (unnecessary in path patterns)
#   (?(   — conditional group
#   (.+)+ — nested quantifiers (catastrophic backtracking risk)
_UNSAFE_PATTERN = re.compile(
    r"\(\?[{#]"  # (?{ code execution, (?# comment
    r"|\(\?\("  # (?( conditional group
    r"|\([^)]*[+*]\)[+*?]"  # nested quantifier: (.+)+, (a*)*, (a+)?
)


class VendorRegistry:
    """Thread-safe singleton registry mapping hostnames to normalised paths.

    All mutable state is held as class attributes rather than instance
    attributes so there is exactly one registry across the process lifetime.
    :meth:`replace` atomically swaps the entire host/pattern tables when a
    remote refresh arrives.

    The class is never instantiated; all methods are ``@classmethod``.
    """

    _lock = threading.Lock()
    _hosts: Dict[str, str] = {}  # host → vendor slug
    _patterns: Dict[str, List[Tuple[re.Pattern, str]]] = {}  # slug → [(pattern, replacement)]
    _version: str = "bundled"

    @classmethod
    def identify(cls, host: str, raw_path: str) -> Optional[Tuple[str, str]]:
        """Look up *host* and return a ``(vendor, normalised_path)`` pair.

        Args:
            host: The bare hostname of the outbound request (no scheme, no
                port), e.g. ``"api.stripe.com"``.
            raw_path: The raw request path, optionally including a query
                string.  The query string is stripped before normalisation.

        Returns:
            A ``(vendor_slug, normalised_path)`` tuple when the host is
            recognised, or ``None`` for unknown vendors.  The slug matches
            the key in the registry (e.g. ``"stripe"``).  The path has IDs
            replaced with placeholders (e.g. ``"/v1/charges/:id"``).

        Thread safety:
            The host lookup and pattern list snapshot are performed under
            the class lock.  Path normalisation is done outside the lock
            using the snapshot so the lock is held as briefly as possible.
        """
        with cls._lock:
            vendor = cls._hosts.get(host)
            vendor_patterns = list(cls._patterns.get(vendor, [])) if vendor else []

        if vendor is None:
            return None

        path = raw_path.split("?")[0]
        path = _apply_vendor_normalizers(vendor_patterns, path)
        path = _apply_generic_normalizers(path)
        return (vendor, path)

    @classmethod
    def load_extra_vendors(cls, extra_vendors: Optional[Dict[str, str]]) -> None:
        """Merge customer-defined host→vendor mappings into the live registry.

        Called once at framework boot after the user's ``configure()`` block
        has run.  Does not affect ``_patterns``; extra vendors use generic
        path normalisation only.

        Args:
            extra_vendors: Mapping of ``{vendor_name: hostname}``.  ``None``
                or an empty dict is a no-op.
        """
        if not extra_vendors:
            return
        with cls._lock:
            for name, host in extra_vendors.items():
                cls._hosts[str(host)] = str(name)

    @classmethod
    def replace(cls, registry_json: dict, extra_vendors: Optional[Dict[str, str]] = None) -> None:
        """Atomically replace the live registry with *registry_json*.

        Rebuilds both the host map and the compiled-pattern map from scratch,
        then re-applies *extra_vendors* so that a registry refresh never
        silently drops customer-defined host mappings.

        Args:
            registry_json: Parsed registry document from the remote endpoint
                or disk cache.  Must have a ``"vendors"`` key.
            extra_vendors: Customer-defined mappings to preserve.  When
                ``None`` the existing extra-vendor entries are not re-applied
                (safe only during initial boot before any extras are loaded).
        """
        new_hosts = _build_hosts(registry_json)
        new_patterns = _build_patterns(registry_json)

        if extra_vendors:
            for name, host in extra_vendors.items():
                new_hosts[str(host)] = str(name)

        with cls._lock:
            cls._hosts = new_hosts
            cls._patterns = new_patterns
            cls._version = str(registry_json.get("version", "unknown"))

        _logger.debug(
            "[Apidepth] Registry updated — version=%s vendors=%d",
            cls._version,
            len(set(new_hosts.values())),
        )

    @classmethod
    def version(cls) -> str:
        """Return the version string of the currently loaded registry.

        Returns ``"bundled"`` until the first successful remote or disk-cache
        load.
        """
        with cls._lock:
            return cls._version

    @classmethod
    def vendor_count(cls) -> int:
        """Return the number of distinct vendor slugs in the live registry."""
        with cls._lock:
            return len(set(cls._hosts.values()))

    @classmethod
    def reset(cls) -> None:
        """Reset the registry to the bundled baseline. Intended for test isolation."""
        with cls._lock:
            cls._hosts = _build_hosts(BUNDLED_BASELINE)
            cls._patterns = _build_patterns(BUNDLED_BASELINE)
            cls._version = BUNDLED_BASELINE["version"]


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _build_hosts(registry: dict) -> Dict[str, str]:
    """Build a ``{hostname: vendor_slug}`` map from a registry document."""
    hosts: Dict[str, str] = {}
    for slug, config in (registry.get("vendors") or {}).items():
        for h in config.get("hosts") or []:
            hosts[h] = slug
    return hosts


def _build_patterns(registry: dict) -> Dict[str, List[Tuple[re.Pattern, str]]]:
    """Compile vendor path patterns from a registry document.

    Patterns that contain unsafe constructs (see :data:`_UNSAFE_PATTERN`) or
    that fail ``re.compile`` are skipped with a warning rather than raising,
    so a bad registry update degrades gracefully rather than crashing.

    Returns:
        Mapping of ``{vendor_slug: [(compiled_pattern, replacement_string)]}``.
    """
    patterns: Dict[str, List[Tuple[re.Pattern, str]]] = {}
    for slug, config in (registry.get("vendors") or {}).items():
        vendor_patterns: List[Tuple[re.Pattern, str]] = []
        for rule in config.get("patterns") or []:
            match_str = rule.get("match", "")
            replace_str = rule.get("replace", "")
            if _UNSAFE_PATTERN.search(match_str):
                _logger.warning(
                    "[Apidepth] Skipping unsafe pattern for %s: %r",
                    _sanitize(slug),
                    match_str,
                )
                continue
            try:
                vendor_patterns.append((re.compile(match_str), replace_str))
            except re.error as exc:
                _logger.warning(
                    "[Apidepth] Skipping invalid pattern for %s %r: %s",
                    _sanitize(slug),
                    match_str,
                    exc,
                )
        patterns[slug] = vendor_patterns
    return patterns


def _apply_vendor_normalizers(rules: List[Tuple[re.Pattern, str]], path: str) -> str:
    """Apply the first matching vendor pattern to *path* and return the result.

    Patterns are tested in declaration order; only the first match is applied.
    If no pattern matches, *path* is returned unchanged.
    """
    for pattern, replacement in rules:
        if pattern.search(path):
            return pattern.sub(replacement, path, count=1)
    return path


def _apply_generic_normalizers(path: str) -> str:
    """Apply all :data:`_GENERIC_PATTERNS` to *path* in order.

    Unlike vendor patterns, all generic patterns are applied (not just the
    first match) so a path with both a UUID segment and a numeric segment
    has both replaced.
    """
    for pattern, replacement in _GENERIC_PATTERNS:
        path = pattern.sub(replacement, path)
    return path


def _sanitize(s: str) -> str:
    """Strip CR/LF/TAB from *s* and truncate to 200 characters.

    Prevents log-injection attacks (CVE-2025-27111 class) when untrusted
    registry content is interpolated into log messages.
    """
    return str(s).translate(str.maketrans("\r\n\t", "   "))[:200]


# ---------------------------------------------------------------------------
# Boot: seed the registry from the bundled baseline immediately on import.
# Logging is not yet configured at this point so _build_patterns warnings
# are suppressed — no bundled pattern is unsafe.
# ---------------------------------------------------------------------------
VendorRegistry._hosts = _build_hosts(BUNDLED_BASELINE)
VendorRegistry._patterns = _build_patterns(BUNDLED_BASELINE)
VendorRegistry._version = BUNDLED_BASELINE["version"]
