"""User-facing configuration for the Apidepth SDK.

All settings are plain attributes on a ``Configuration`` instance.  The
singleton is created lazily by ``apidepth.get_configuration()`` and mutated
by ``apidepth.configure(**kwargs)``.  Framework integrations (Django, Flask)
read their own config sources and write into the same singleton during
``AppConfig.ready()`` / ``Apidepth.init_app()``.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional


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
            should never be recorded.  Checked before vendor lookup.
            Example: ``["internal.mycompany.com"]``.

        on_flush_error:
            Optional callable invoked whenever a batch flush fails.
            Signature: ``fn(exc: Exception, ctx: dict) -> None`` where
            *ctx* contains ``dropped_events``, ``consecutive_failures``,
            and ``total_dropped``.  Exceptions raised inside the callback
            are swallowed so they never crash the flush thread.

        environment:
            Free-form deployment environment tag included in every event.
            Framework integrations set this automatically from
            ``Django.env`` / ``Flask.config["ENV"]``.
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

    def __init__(self) -> None:
        self.api_key: Optional[str] = None
        self.collector_url: Optional[str] = None
        self.enabled: bool = True
        self.flush_interval: int = 20
        self.registry_refresh_interval: int = 6 * 60 * 60
        self.registry_cache_path: str = "/tmp/apidepth_registry.json"
        self.ignored_hosts: List[str] = []
        self.on_flush_error: Optional[Callable[[Exception, Dict], None]] = None
        self.environment: Optional[str] = None
        self.sample_rate: float = 1.0
        self.extra_vendors: Dict[str, str] = {}

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Configuration("
            f"api_key={'***' if self.api_key else None!r}, "
            f"environment={self.environment!r}, "
            f"enabled={self.enabled!r}, "
            f"sample_rate={self.sample_rate!r})"
        )
