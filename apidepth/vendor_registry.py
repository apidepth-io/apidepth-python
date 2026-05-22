from __future__ import annotations

import logging
import re
import threading
from typing import Dict, List, Optional, Tuple

_logger = logging.getLogger("apidepth")

BUNDLED_BASELINE: dict = {
    "version": "bundled",
    "vendors": {
        "stripe": {
            "hosts": ["api.stripe.com"],
            "patterns": [
                {"match": r"/v1/charges/ch_\w+",           "replace": "/v1/charges/:id"},
                {"match": r"/v1/customers/cus_\w+",        "replace": "/v1/customers/:id"},
                {"match": r"/v1/payment_intents/pi_\w+",   "replace": "/v1/payment_intents/:id"},
                {"match": r"/v1/subscriptions/sub_\w+",    "replace": "/v1/subscriptions/:id"},
                {"match": r"/v1/invoices/in_\w+",          "replace": "/v1/invoices/:id"},
                {"match": r"/v1/refunds/re_\w+",           "replace": "/v1/refunds/:id"},
            ],
        },
        "openai": {
            "hosts": ["api.openai.com"],
            "patterns": [
                {"match": r"/v1/chat/completions",         "replace": "/v1/chat/completions"},
                {"match": r"/v1/embeddings",               "replace": "/v1/embeddings"},
                {"match": r"/v1/images/generations",       "replace": "/v1/images/generations"},
                {"match": r"/v1/files/file-\w+",           "replace": "/v1/files/:id"},
            ],
        },
        "anthropic": {
            "hosts": ["api.anthropic.com"],
            "patterns": [
                {"match": r"/v1/messages",                 "replace": "/v1/messages"},
            ],
        },
        "twilio": {
            "hosts": ["api.twilio.com"],
            "patterns": [
                {"match": r"/2010-04-01/Accounts/AC\w+/Messages/SM\w+", "replace": "/Accounts/:id/Messages/:id"},
                {"match": r"/2010-04-01/Accounts/AC\w+/Messages",       "replace": "/Accounts/:id/Messages"},
                {"match": r"/2010-04-01/Accounts/AC\w+/Calls/CA\w+",    "replace": "/Accounts/:id/Calls/:id"},
                {"match": r"/2010-04-01/Accounts/AC\w+/Calls",          "replace": "/Accounts/:id/Calls"},
            ],
        },
        "resend": {
            "hosts": ["api.resend.com"],
            "patterns": [
                {"match": r"/emails/[0-9a-f-]{36}",        "replace": "/emails/:id"},
            ],
        },
        "github": {
            "hosts": ["api.github.com"],
            "patterns": [
                {"match": r"/repos/[^/]+/[^/]+/pulls/\d+",  "replace": "/repos/:owner/:repo/pulls/:number"},
                {"match": r"/repos/[^/]+/[^/]+/issues/\d+", "replace": "/repos/:owner/:repo/issues/:number"},
                {"match": r"/repos/[^/]+/[^/]+",            "replace": "/repos/:owner/:repo"},
                {"match": r"/users/[^/]+",                  "replace": "/users/:username"},
            ],
        },
    },
}

# Applied in order after vendor-specific patterns; first match wins per segment.
_GENERIC_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "/:uuid"),
    (re.compile(r"/\d{4,}"),        "/:id"),
    (re.compile(r"/[a-z0-9]{24,}"), "/:token"),
]

# Patterns that can enable arbitrary code execution inside Python's re engine;
# legitimate path normalisation rules never need these.
_UNSAFE_PATTERN = re.compile(r"\(\?[{<!=]|\(\?#|\+\?\*\?\?")


class VendorRegistry:
    _lock = threading.Lock()
    _hosts: Dict[str, str] = {}
    _patterns: Dict[str, List[Tuple[re.Pattern, str]]] = {}
    _version: str = "bundled"

    @classmethod
    def identify(cls, host: str, raw_path: str) -> Optional[Tuple[str, str]]:
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
        if not extra_vendors:
            return
        with cls._lock:
            for name, host in extra_vendors.items():
                cls._hosts[str(host)] = str(name)

    @classmethod
    def replace(cls, registry_json: dict, extra_vendors: Optional[Dict[str, str]] = None) -> None:
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
        with cls._lock:
            return cls._version

    @classmethod
    def vendor_count(cls) -> int:
        with cls._lock:
            return len(set(cls._hosts.values()))


def _build_hosts(registry: dict) -> Dict[str, str]:
    hosts: Dict[str, str] = {}
    for slug, config in (registry.get("vendors") or {}).items():
        for h in config.get("hosts") or []:
            hosts[h] = slug
    return hosts


def _build_patterns(registry: dict) -> Dict[str, List[Tuple[re.Pattern, str]]]:
    patterns: Dict[str, List[Tuple[re.Pattern, str]]] = {}
    for slug, config in (registry.get("vendors") or {}).items():
        vendor_patterns: List[Tuple[re.Pattern, str]] = []
        for rule in config.get("patterns") or []:
            match_str = rule.get("match", "")
            replace_str = rule.get("replace", "")
            if _UNSAFE_PATTERN.search(match_str):
                _logger.warning(
                    "[Apidepth] Skipping unsafe pattern for %s: %r",
                    _sanitize(slug), match_str,
                )
                continue
            try:
                vendor_patterns.append((re.compile(match_str), replace_str))
            except re.error as exc:
                _logger.warning(
                    "[Apidepth] Skipping invalid pattern for %s %r: %s",
                    _sanitize(slug), match_str, exc,
                )
        patterns[slug] = vendor_patterns
    return patterns


def _apply_vendor_normalizers(rules: List[Tuple[re.Pattern, str]], path: str) -> str:
    for pattern, replacement in rules:
        if pattern.search(path):
            return pattern.sub(replacement, path)
    return path


def _apply_generic_normalizers(path: str) -> str:
    for pattern, replacement in _GENERIC_PATTERNS:
        path = pattern.sub(replacement, path)
    return path


def _sanitize(s: str) -> str:
    return str(s).translate(str.maketrans("\r\n\t", "   "))[:200]


# Boot the registry from the bundled baseline immediately on import.
VendorRegistry._hosts = _build_hosts(BUNDLED_BASELINE)
VendorRegistry._patterns = _build_patterns(BUNDLED_BASELINE)
VendorRegistry._version = BUNDLED_BASELINE["version"]
