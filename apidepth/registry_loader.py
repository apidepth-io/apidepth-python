"""Remote vendor-registry fetcher, disk-cache manager, and bidirectional sync.

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

Bidirectional sync
------------------
Each successful remote fetch may include two additional top-level keys:

``customer_vendors``
    A ``{vendor_name: hostname}`` dict of vendors added via the Apidepth
    dashboard.  These are loaded into the vendor registry via
    :meth:`~apidepth.vendor_registry.VendorRegistry.load_extra_vendors` so
    they take effect immediately, before the registry replacement lands.
    Only ``str → str`` entries are accepted; non-string keys or values are
    silently dropped.

    When a name in ``customer_vendors`` matches a name in local
    ``extra_vendors`` but with a **different host**, a one-time warning is
    emitted so the developer knows the registry is overriding their local
    configuration.

``warnings``
    A block of developer-facing advisory messages emitted by the collector:

    ``stale_vendors``
        List of vendor names that have not sent events in 7+ days.  A
        one-time warning is logged per vendor so the developer knows to
        either remove the mapping or investigate the integration.

Both warning categories follow a **warn-once-per-process-lifetime** pattern:
a per-vendor flag prevents log spam in long-running processes.  The
conflict-vendor table is cleared after each emit so that a vendor whose host
changes between registry fetches produces a fresh warning.

This module mirrors the behaviour of the Ruby gem's ``RegistryLoader`` class
added in commits ``f2882dd`` and ``2161fd0``.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import ssl
import time
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

_logger = logging.getLogger("apidepth")

#: Registry endpoint.  Fixed — not customer-configurable.
REGISTRY_URL = "https://collector.apidepth.io/v1/registry"

#: Maximum registry response size in bytes.  A legitimate registry is ~10 KB;
#: 512 KB is a generous ceiling that guards against pathological responses
#: from a compromised or misconfigured endpoint consuming unbounded memory.
MAX_RESPONSE_BYTES = 512_000

# ---------------------------------------------------------------------------
# Module-level warn-once state (mirrors RegistryLoader class-level ivars in Ruby).
# All three dicts are protected by _lock.
# ---------------------------------------------------------------------------

#: Lock protecting _conflict_vendors, _warned_stale, and _warned_conflict.
#: Initialized at module import time — the same pattern as VendorRegistry._lock.
_lock = threading.Lock()

#: Conflicts detected during the current fetch cycle.
#: Maps vendor_name → {"local": local_host, "remote": remote_host}.
#: Cleared by _emit_conflict_warnings after each emit so that a host change
#: on the next fetch cycle produces a fresh warning.
_conflict_vendors: Dict[str, Dict[str, str]] = {}

#: Vendors for which a stale warning has already been emitted this process lifetime.
#: Maps vendor_name → True.
_warned_stale: Dict[str, bool] = {}

#: Vendors for which a conflict warning has already been emitted this process lifetime.
#: Maps vendor_name → True.
_warned_conflict: Dict[str, bool] = {}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


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

    On a successful 200 response the method:

    1. Validates the response size.
    2. Applies ``customer_vendors`` from the payload via
       :func:`_apply_customer_vendors`.
    3. Emits developer warnings via :func:`_emit_warnings`.
    4. Warms the disk cache.
    5. Returns the parsed registry dict.

    This ordering mirrors the Ruby gem exactly: customer vendors and warnings
    are processed before :meth:`~apidepth.vendor_registry.VendorRegistry.replace`
    rebuilds the host table in the caller.

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
        conn = http.client.HTTPSConnection(
            parsed.hostname, parsed.port or 443, timeout=5, context=ctx
        )
        conn.request(
            "GET", parsed.path, headers={"Authorization": f"Bearer {config.api_key or ''}"}
        )
        resp = conn.getresponse()
        if resp.status != 200:
            _logger.debug(
                "[Apidepth] Registry fetch returned HTTP %d — using cached/bundled baseline",
                resp.status,
            )
            return None

        # Read one byte beyond the limit so we can detect over-sized responses
        # without fully buffering them.
        body = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            _logger.warning(
                "[Apidepth] Registry response too large (%d bytes) — skipping", len(body)
            )
            return None

        registry = json.loads(body)

        # Apply collector-managed customer vendors and surface developer warnings
        # before the caller calls VendorRegistry.replace().
        _apply_customer_vendors(registry, config)
        _emit_warnings(registry)

        _write_cache(config, body)
        return registry
    except Exception as exc:
        _logger.debug(
            "[Apidepth] Registry fetch failed: %s: %s", type(exc).__name__, _sanitize(str(exc))
        )
        return None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _apply_customer_vendors(registry: Dict[str, Any], config: Any) -> None:
    """Load collector-managed ``customer_vendors`` into the vendor registry.

    Reads the ``customer_vendors`` key from *registry* (a ``{name: host}``
    dict pushed by the Apidepth collector when a customer adds a vendor via
    the dashboard).  Filters the payload to ``str → str`` entries only —
    non-string keys or values are silently dropped so a malformed collector
    response cannot inject garbage vendor names.

    Before loading, compares each remote host against the same vendor name
    in local ``extra_vendors``.  If the hosts differ, the conflict is recorded
    in ``_conflict_vendors`` so :func:`_emit_conflict_warnings` can surface
    a one-time warning.

    Finally, the clean dict is passed to
    :meth:`~apidepth.vendor_registry.VendorRegistry.load_extra_vendors`
    which merges the entries into the live host table under the registry lock.

    Args:
        registry: The parsed registry response dict.
        config: The current :class:`~apidepth.configuration.Configuration`
            (used to read local ``extra_vendors`` for conflict detection).
    """
    remote = registry.get("customer_vendors")
    if not isinstance(remote, dict) or not remote:
        return

    local = config.extra_vendors or {}
    clean: Dict[str, str] = {}

    for name, remote_host in remote.items():
        # Drop any entry whose key or value is not a plain string.  A non-string
        # key like 42 would be silently coerced to "42" by load_extra_vendors,
        # registering a nonsense vendor name the developer cannot see.
        if not isinstance(name, str) or not isinstance(remote_host, str):
            continue

        clean[name] = remote_host

        # Record a conflict if the developer has locally configured the same
        # vendor name with a different host.  We only track the conflict here;
        # the warning is emitted (once) by _emit_conflict_warnings.
        local_host = local.get(name)
        if local_host and local_host != remote_host:
            with _lock:
                _conflict_vendors[name] = {"local": local_host, "remote": remote_host}

    from apidepth.vendor_registry import VendorRegistry

    VendorRegistry.load_extra_vendors(clean)


def _emit_warnings(registry: Dict[str, Any]) -> None:
    """Dispatch developer-facing warnings from the registry response.

    Handles two warning categories:

    * **Stale vendors** — sourced from ``registry["warnings"]["stale_vendors"]``.
      Only present in responses from collector v0.3+; older or cached
      responses without the key are silently skipped.
    * **Host conflicts** — accumulated by :func:`_apply_customer_vendors`
      during the current fetch cycle, emitted regardless of whether the
      registry includes a ``warnings`` block.

    Args:
        registry: The parsed registry response dict.
    """
    warnings = registry.get("warnings")
    if isinstance(warnings, dict):
        _emit_stale_warnings(warnings.get("stale_vendors", []))

    _emit_conflict_warnings()


def _emit_stale_warnings(stale: Any) -> None:
    """Log a one-time warning for each vendor in *stale*.

    A stale vendor is one the collector has not received events for in 7+
    days, indicating the integration may be broken or the vendor mapping
    should be removed.

    The warn-once flag is set inside the lock; the log call is made outside
    to avoid holding the lock during I/O.

    Args:
        stale: The value of ``registry["warnings"]["stale_vendors"]``.
            Expected to be a list of vendor-name strings.  Non-list values
            and non-string list entries are silently ignored.
    """
    if not isinstance(stale, list):
        return

    to_warn: List[str] = []
    with _lock:
        for name in stale:
            if not isinstance(name, str):
                continue
            if not _warned_stale.get(name):
                _warned_stale[name] = True
                to_warn.append(name)

    for name in to_warn:
        _logger.warning(
            "[Apidepth] No events received from '%s' in 7+ days — "
            "is it still declared in extra_vendors? If intentional, remove "
            "it at www.apidepth.io.",
            name,
        )


def _emit_conflict_warnings() -> None:
    """Log a one-time warning for each vendor with a host conflict.

    A conflict occurs when a vendor name appears in both local ``extra_vendors``
    and the registry's ``customer_vendors`` but with different hosts.  The
    registry host takes precedence; this warning surfaces the discrepancy so
    the developer can reconcile their local configuration.

    The conflict table is cleared after reading so that a host change on the
    next fetch cycle produces a fresh warning even if the vendor name was
    warned about before.

    Warn-once behaviour: each vendor name is logged at most once per process
    lifetime regardless of how many fetch cycles detect the conflict.
    """
    with _lock:
        pending = dict(_conflict_vendors)
        _conflict_vendors.clear()
        to_warn = {name: hosts for name, hosts in pending.items() if not _warned_conflict.get(name)}
        for name in to_warn:
            _warned_conflict[name] = True

    for name, hosts in to_warn.items():
        _logger.warning(
            "[Apidepth] extra_vendors conflict: '%s' is configured as "
            "'%s' locally but the registry has '%s' "
            "— registry takes precedence. Update your initializer or remove "
            "the entry from your dashboard at www.apidepth.io.",
            name,
            hosts["local"],
            hosts["remote"],
        )


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
        raise ValueError(
            f"registry_cache_path must not contain '..' traversal segments (got {path!r})"
        )


def _sanitize(s: str) -> str:
    """Strip CR/LF/TAB from *s* and truncate to 200 characters.

    Prevents log-injection when error messages from disk I/O are written to
    the logger.
    """
    return str(s).translate(str.maketrans("\r\n\t", "   "))[:200]
