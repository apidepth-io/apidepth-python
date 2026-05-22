"""Remote vendor-registry fetcher and disk-cache manager.

The registry is a JSON document published by the Apidepth collector that
extends the :data:`~apidepth.vendor_registry.BUNDLED_BASELINE` with new
vendors and updated path patterns.  Loading it at startup means customers
get support for new vendors without upgrading the SDK.

Load priority on startup (highest to lowest):

1. **Remote fetch** — ``GET /v1/registry`` authenticated with the API key.
2. **Disk cache** — the registry JSON written by the previous successful
   fetch (default ``/tmp/apidepth_registry.json``).
3. **Bundled baseline** — always available, pre-seeded at import time in
   :mod:`apidepth.vendor_registry`.

A background thread (``apidepth-registry``) repeats the remote fetch every
``registry_refresh_interval`` seconds (default 6 hours) so the process picks
up new vendors without restarting.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import ssl
import time
import threading
from typing import Any, Dict, Optional
from urllib.parse import urlparse

_logger = logging.getLogger("apidepth")

#: Registry endpoint.  Fixed — not customer-configurable.
REGISTRY_URL = "https://collector.apidepth.io/v1/registry"

#: Maximum registry response size in bytes.  A legitimate registry is ~10 KB;
#: 512 KB is a generous ceiling that guards against pathological responses
#: from a compromised or misconfigured endpoint consuming unbounded memory.
MAX_RESPONSE_BYTES = 512_000


def load_and_start() -> None:
    """Bootstrap the registry and start the background refresh thread.

    Performs a synchronous load at call time (remote → disk cache → bundled
    baseline) so the best available data is in place before the first
    outbound request is instrumented.  Then launches a daemon thread that
    repeats the remote fetch every ``registry_refresh_interval`` seconds.

    Called automatically by framework integrations (Django ``AppConfig.ready``,
    Flask ``Apidepth.init_app``).  Can be called manually for non-framework
    setups, but is not required — the bundled baseline is always active.
    """
    from apidepth.vendor_registry import VendorRegistry
    import apidepth

    config = apidepth.get_configuration()
    registry = _fetch_remote(config) or _load_from_disk(config)
    if registry:
        VendorRegistry.replace(registry, config.extra_vendors or {})

    _start_refresh_thread()


def _start_refresh_thread() -> threading.Thread:
    """Spawn the background registry-refresh daemon thread.

    The thread reads ``registry_refresh_interval`` from the live
    configuration on each iteration so changes made after startup are
    respected without a restart.

    Returns:
        The started :class:`threading.Thread` (daemon, name
        ``"apidepth-registry"``).
    """
    def _loop() -> None:
        while True:
            import apidepth
            cfg = apidepth.get_configuration()
            time.sleep(cfg.registry_refresh_interval)
            registry = _fetch_remote(cfg)
            if registry:
                from apidepth.vendor_registry import VendorRegistry
                VendorRegistry.replace(registry, cfg.extra_vendors or {})

    t = threading.Thread(target=_loop, name="apidepth-registry", daemon=True)
    t.start()
    return t


def _fetch_remote(config: Any) -> Optional[Dict[str, Any]]:
    """Fetch the registry JSON from the remote endpoint.

    Uses the stdlib ``http.client`` directly (not the monkey-patched
    *requests* layer) so the registry fetch is never self-recorded as an
    instrumented event.

    Args:
        config: The current :class:`~apidepth.configuration.Configuration`.

    Returns:
        The parsed registry dict on success, or ``None`` on any error
        (network failure, non-200 status, response too large, JSON parse
        error).  Errors are intentionally swallowed — the caller falls back
        to disk cache or the bundled baseline.
    """
    conn: Optional[http.client.HTTPSConnection] = None
    try:
        parsed = urlparse(REGISTRY_URL)
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=5, context=ctx)
        conn.request("GET", parsed.path, headers={"Authorization": f"Bearer {config.api_key or ''}"})
        resp = conn.getresponse()
        if resp.status != 200:
            return None

        # Read one byte beyond the limit so we can detect over-sized responses
        # without fully buffering them.
        body = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            _logger.warning("[Apidepth] Registry response too large (%d bytes) — skipping", len(body))
            return None

        registry = json.loads(body)
        _write_cache(config, body)
        return registry
    except Exception:
        return None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _load_from_disk(config: Any) -> Optional[Dict[str, Any]]:
    """Read the registry JSON from the disk cache.

    Validates the cache path before any filesystem access to prevent
    path-traversal issues from a misconfigured ``registry_cache_path``.

    Args:
        config: The current :class:`~apidepth.configuration.Configuration`.

    Returns:
        The parsed registry dict, or ``None`` if the file does not exist,
        the path is invalid, or the file cannot be parsed.
    """
    try:
        path = config.registry_cache_path
        _validate_cache_path(path)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return json.loads(f.read())
    except ValueError as exc:
        _logger.warning("[Apidepth] Invalid registry_cache_path: %s", exc)
        return None
    except Exception as exc:
        _logger.warning("[Apidepth] Could not read registry cache: %s", _sanitize(str(exc)))
        return None


def _write_cache(config: Any, body: bytes) -> None:
    """Write the raw registry response bytes to the disk cache.

    A warm cache means subsequent cold-starts can skip the remote fetch and
    start with up-to-date vendor data immediately.

    Args:
        config: The current :class:`~apidepth.configuration.Configuration`.
        body: The raw (bytes) registry response body to persist.
    """
    try:
        path = config.registry_cache_path
        _validate_cache_path(path)
        with open(path, "wb") as f:
            f.write(body)
    except ValueError as exc:
        _logger.warning("[Apidepth] Invalid registry_cache_path: %s", exc)
    except Exception as exc:
        _logger.warning("[Apidepth] Could not write registry cache: %s", _sanitize(str(exc)))


def _validate_cache_path(path: str) -> None:
    """Raise ``ValueError`` if *path* is not a safe absolute filesystem path.

    Requires an absolute path (starts with ``/``) with no ``..`` traversal
    segments.  Without this guard, a misconfigured path like
    ``"../../etc/cron.d/apidepth"`` would cause the SDK to write the
    registry JSON into sensitive system directories.

    Args:
        path: The candidate cache path string.

    Raises:
        ValueError: If *path* is not a string, does not start with ``/``,
            or contains a ``..`` segment.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"registry_cache_path must be an absolute path (got {path!r})")
    if ".." in path.split("/"):
        raise ValueError(f"registry_cache_path must not contain '..' traversal segments (got {path!r})")


def _sanitize(s: str) -> str:
    """Strip CR/LF/TAB from *s* and truncate to 200 characters.

    Prevents log-injection when error messages from disk I/O are written to
    the logger.
    """
    return str(s).translate(str.maketrans("\r\n\t", "   "))[:200]
