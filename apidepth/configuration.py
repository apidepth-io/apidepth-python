"""User-facing configuration for the Apidepth SDK.

All settings are plain attributes on a ``Configuration`` instance.  The
singleton is created lazily by ``apidepth.get_configuration()`` and mutated
by ``apidepth.configure(**kwargs)``.  Framework integrations (Django, Flask)
read their own config sources and write into the same singleton during
``AppConfig.ready()`` / ``Apidepth.init_app()``.
"""

from __future__ import annotations

import fnmatch
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple, Union
from urllib.parse import urlparse


# Always ignored regardless of user config. Covers unambiguous loopback
# addresses only — wildcard internal patterns are not pre-populated because
# silently swallowing traffic the developer wants to see is worse than showing
# mystery vendors. The setup subcommand prompts for custom patterns.
_HARD_IGNORED_HOSTS: Tuple[str, ...] = ("localhost", "127.0.0.1", "0.0.0.0", "::1")  # nosec B104


class Configuration:
    """Holds all runtime settings for the SDK.

    Attributes:
        api_key:
            Your Apidepth API key.  Required — events are silently dropped
            until this is set.  Obtain one at https://www.apidepth.io.

        collector_url:
            Override the default collector endpoint.  Must use HTTPS and
            must not resolve to a private or loopback address.  Leave
            ``None`` to use the production endpoint.

        enabled:
            Master on/off switch.  Set to ``False`` to disable all
            instrumentation without unpatching HTTP clients.  Default: ``True``.

        flush_interval:
            Seconds between background queue flushes.  Default: ``20``.

        registry_refresh_interval:
            Seconds between remote vendor-registry refreshes.
            Default: ``21600`` (6 hours).

        registry_cache_path:
            Absolute filesystem path where the registry JSON is cached
            between process restarts.  Must start with ``/`` and must not
            contain ``..`` segments.  Default: ``"/tmp/apidepth_registry.json"``.

        ignored_hosts:
            List of hostnames (no scheme, no port) whose outbound requests
            should never be recorded.  Glob wildcards are supported
            (``*`` matches any sequence, ``?`` matches one character).
            Hard defaults (localhost, 127.0.0.1, 0.0.0.0, ::1) are always
            present and cannot be removed.
            Example: ``["*.internal", "db.mycompany.local"]``.

        on_flush_error:
            Optional callable invoked whenever a batch flush fails.
            Signature: ``fn(exc: Exception, ctx: dict) -> None`` where
            *ctx* contains ``dropped_events``, ``consecutive_failures``,
            and ``total_dropped``.  Exceptions raised inside the callback
            are swallowed so they never crash the flush thread.

        environment:
            Free-form deployment environment tag included in every event.
            Set via ``apidepth.configure(environment=...)``,
            ``APIDEPTH = {"environment": ...}`` in Django settings, or
            ``APIDEPTH_ENVIRONMENT`` in Flask config.
            Example: ``"production"``, ``"staging"``.

        sample_rate:
            Fraction of requests to capture.  ``1.0`` captures everything
            (default).  ``0.5`` captures roughly half.  ``0.0`` captures
            nothing.  Applied before vendor lookup so the cost of
            unrecognised-host checks is also avoided for skipped requests.

        extra_vendors:
            Map of ``{vendor_name: hostname}`` for in-house APIs that are
            not in the bundled registry.  Example:
            ``{"payments-api": "api.payments.internal"}``.
            These mappings survive remote registry refreshes.
    """

    #: Canonical set of valid configuration keys. Used by configure() to
    #: validate kwargs without creating a throwaway instance.
    VALID_KEYS: frozenset = frozenset(
        {
            "api_key",
            "collector_url",
            "enabled",
            "flush_interval",
            "registry_refresh_interval",
            "registry_cache_path",
            "ignored_hosts",
            "on_flush_error",
            "environment",
            "sample_rate",
            "extra_vendors",
            "capture_model_names",
        }
    )

    def __init__(self) -> None:
        self.api_key: Optional[str] = None
        self._collector_url: Optional[str] = None
        self.enabled: bool = True
        self.flush_interval: int = 20
        self.registry_refresh_interval: int = 6 * 60 * 60
        self.registry_cache_path: str = "/tmp/apidepth_registry.json"  # nosec B108 — public read-only registry cache, not sensitive data
        self._user_hosts: List[str] = []
        self.on_flush_error: Optional[Callable[[Exception, Dict], None]] = None
        self.environment: Optional[str] = None
        self.sample_rate: float = 1.0
        self.extra_vendors: Dict[str, str] = {}
        self.capture_model_names: bool = True
        self._rebuild_ignored_hosts()

    @property
    def collector_url(self) -> Optional[str]:
        return self._collector_url

    @collector_url.setter
    def collector_url(self, value: Optional[str]) -> None:
        self._collector_url = value
        self._rebuild_ignored_hosts()

    @property
    def ignored_hosts(self) -> FrozenSet[str]:
        """All ignored hostnames (hard defaults + user-configured), as a frozenset."""
        return self._ignored_hosts

    @ignored_hosts.setter
    def ignored_hosts(self, value: Union[Iterable[str], None]) -> None:
        self._user_hosts = list(value) if value else []
        self._rebuild_ignored_hosts()

    def ignored_host(self, host: str) -> bool:
        """Return True if *host* should be skipped.

        Supports glob wildcards (``*``, ``?``) so customers can ignore entire
        internal domains, e.g. ``"*.internal"`` or ``"*.svc.cluster.local"``.
        Exact matches are checked first (O(1)); glob patterns are checked only
        when no exact match is found.
        """
        if host in self._exact_ignored:
            return True
        return any(fnmatch.fnmatch(host, pat) for pat in self._glob_patterns)

    def _rebuild_ignored_hosts(self) -> None:
        all_hosts: List[str] = list(_HARD_IGNORED_HOSTS) + self._user_hosts
        if self._collector_url:
            try:
                parsed_host = urlparse(self._collector_url).hostname
                if parsed_host:
                    all_hosts.append(parsed_host)
            except Exception:
                pass
        self._exact_ignored: frozenset = frozenset(
            h for h in all_hosts if "*" not in h and "?" not in h
        )
        self._glob_patterns: List[str] = [h for h in all_hosts if "*" in h or "?" in h]
        self._ignored_hosts: FrozenSet[str] = frozenset(all_hosts)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Configuration("
            f"api_key={'***' if self.api_key else None!r}, "
            f"environment={self.environment!r}, "
            f"enabled={self.enabled!r}, "
            f"sample_rate={self.sample_rate!r})"
        )
