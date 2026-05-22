"""Background event queue and HTTP transport for the Apidepth SDK.

Architecture
------------
``Collector`` is a process-wide singleton accessed via
:meth:`Collector.instance`.  It owns:

* A bounded ``queue.Queue`` (max 5 000 events) that instrumentation writes
  into without blocking.
* A **flush thread** (``apidepth-flush``) that drains up to 100 events every
  ``flush_interval`` seconds and POSTs them to the collector endpoint.
* A **watchdog thread** (``apidepth-watchdog``) that restarts the flush
  thread if it ever dies unexpectedly.
* An ``atexit`` handler that does a final synchronous flush so in-flight
  events are not lost on graceful shutdown.

All threads are daemon threads so they never prevent the interpreter from
exiting.

Transport
---------
Batches are sent over a persistent ``http.client.HTTPSConnection`` with
``keep_alive_timeout=30`` (implicit in Python's stdlib).  The connection is
established lazily on the first flush and reused across batches.  It is
closed and cleared on any error so the next flush attempt opens a fresh
connection.

The collector endpoint URL is validated once on first use
(HTTPS-only, no private/loopback addresses) to prevent SSRF.

Fork safety
-----------
Call :meth:`Collector.reset` inside ``os.register_at_fork`` or a framework
equivalent (e.g. ``on_worker_boot`` in Puma-style servers) to give each
worker process its own flush thread.
"""
from __future__ import annotations

import atexit
import http.client
import json
import logging
import queue
import re
import ssl
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import ParseResult, urlparse

_logger = logging.getLogger("apidepth")

#: Maximum events drained from the queue in a single flush.
MAX_BATCH_SIZE = 100

#: Maximum events held in the queue before new events are dropped.
#: Prevents unbounded memory growth when the collector endpoint is down.
MAX_QUEUE_SIZE = 5_000

#: Number of consecutive flush failures before a warning is logged.
FAILURE_THRESHOLD = 3

#: Seconds between watchdog checks on the flush thread health.
WATCHDOG_INTERVAL = 60

#: Default collector endpoint used when ``Configuration.collector_url`` is unset.
DEFAULT_URL = "https://collector.apidepth.io/v1/events"

# Matches hostnames that must never be used as a collector endpoint.
# Covers localhost, loopback, link-local, private RFC-1918, and IPv6 equivalents.
_PRIVATE_HOST_RE = re.compile(
    r"""
    \Alocalhost\Z          |
    \A127\.                |
    \A0\.0\.0\.0\Z         |
    \A169\.254\.           |
    \A10\.                 |
    \A172\.(1[6-9]|2\d|3[01])\. |
    \A192\.168\.           |
    \A\[?::1\]?\Z          |
    \A\[?fc                |
    \A\[?fe80:
    """,
    re.VERBOSE | re.IGNORECASE,
)


class Collector:
    """Process-wide singleton that buffers events and flushes them in bulk.

    Do not instantiate this class directly.  Use :meth:`instance` to obtain
    the singleton and :meth:`reset` for fork-safety.
    """

    _instance: Optional["Collector"] = None
    _instance_lock = threading.Lock()
    _fork_safety_registered: bool = False

    # ------------------------------------------------------------------
    # Singleton lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "Collector":
        """Return the process-wide ``Collector`` singleton, creating it if needed.

        Thread-safe.  The first call starts the flush and watchdog threads
        and registers the ``atexit`` flush handler.
        """
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Destroy the current singleton so the next :meth:`instance` call starts fresh.

        Call this inside a post-fork hook so each worker process gets its
        own flush thread.  Without it, forked workers inherit the parent's
        stale singleton whose flush thread was not copied by ``fork()``.

        :meth:`register_fork_safety` calls this automatically via
        ``os.register_at_fork`` on POSIX platforms.  Manual calls are only
        needed on Windows or inside server-specific hooks (e.g. uWSGI's
        ``@postfork`` decorator).

        Example (Gunicorn ``config.py``, only needed on Windows)::

            def post_fork(server, worker):
                from apidepth.collector import Collector
                Collector.reset()
        """
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance._teardown()
            cls._instance = None

    @classmethod
    def register_fork_safety(cls) -> None:
        """Register a post-fork hook so each worker process gets its own Collector.

        Uses :func:`os.register_at_fork` (Python 3.7+, POSIX only) to call
        :meth:`reset` in the child process immediately after ``fork()``.
        Without this, forked workers inherit the parent's stale singleton
        whose background flush thread was not copied by ``os.fork()`` — events
        recorded in workers would never be flushed.

        Safe to call multiple times — only the first call registers the hook.

        This mirrors the Ruby gem's ``ActiveSupport::ForkTracker.after_fork``
        integration added in the Railtie (Rails 7.1+).

        If ``os.register_at_fork`` is not available (Windows) *and* a forking
        server is detected, a one-time warning is logged so the developer
        knows to add a manual hook.
        """
        with cls._instance_lock:
            if cls._fork_safety_registered:
                return
            try:
                import os
                os.register_at_fork(after_in_child=cls.reset)
                cls._fork_safety_registered = True
                _logger.debug("[Apidepth] Fork safety registered via os.register_at_fork")
            except AttributeError:
                # os.register_at_fork is POSIX-only; not available on Windows.
                cls._fork_safety_registered = True  # mark done so we only warn once
                import sys
                mods = sys.modules
                forking_server = next(
                    (s for s in ("gunicorn", "uwsgi") if s in mods), None
                )
                if forking_server:
                    _logger.warning(
                        "[Apidepth] %s detected but os.register_at_fork is unavailable "
                        "on this platform. Workers in multiprocess mode will not flush "
                        "events. Add Collector.reset() to your server's post-fork hook.",
                        forking_server,
                    )

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self._stats_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._consecutive_failures: int = 0
        self._total_dropped: int = 0
        self._last_flush_at: Optional[float] = None
        self._conn: Optional[http.client.HTTPSConnection] = None
        self._cached_url: Optional[ParseResult] = None
        self._warned_no_key: bool = False
        self._flush_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._start_flush_thread()
        self._start_watchdog_thread()
        atexit.register(self.flush)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, event: Dict[str, Any]) -> None:
        """Enqueue *event* for the next batch flush.

        Non-blocking.  If the queue is full the event is silently dropped
        and ``total_dropped`` is incremented.  This prevents back-pressure
        from propagating into the caller's hot path.

        Args:
            event: A validated event dict produced by :func:`apidepth.event.build`.
        """
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._stats_lock:
                self._total_dropped += 1

    def flush(self) -> None:
        """Drain the queue and send all pending events synchronously.

        Called automatically by the ``atexit`` handler on graceful shutdown.
        Can also be called manually (e.g. in tests or before a ``fork``).

        Unlike the background :meth:`_safe_flush`, this method always logs
        at ``WARNING`` level on failure regardless of the consecutive-failure
        count.
        """
        events = self._drain_queue()
        if not events:
            return
        try:
            self._send_batch(events)
            with self._stats_lock:
                self._consecutive_failures = 0
                self._last_flush_at = time.time()
        except Exception as exc:
            with self._stats_lock:
                self._consecutive_failures += 1
                failures = self._consecutive_failures
            self._invoke_error_callback(exc, len(events), failures)
            _logger.warning("[Apidepth] Final flush failed: %s: %s", type(exc).__name__, exc)

    def stats(self) -> Dict[str, Any]:
        """Return a snapshot of the collector's operational metrics.

        Returns:
            A dict with four keys:

            ``queue_size`` (int)
                Current number of events waiting to be flushed.

            ``consecutive_failures`` (int)
                Number of flush attempts that have failed in a row.
                Resets to 0 on the next successful flush.

            ``total_dropped`` (int)
                Cumulative number of events discarded due to a full queue.

            ``last_flush_at`` (float | None)
                ``time.time()`` value of the last *successful* flush, or
                ``None`` if no flush has succeeded yet.
        """
        with self._stats_lock:
            return {
                "queue_size": self._queue.qsize(),
                "consecutive_failures": self._consecutive_failures,
                "total_dropped": self._total_dropped,
                "last_flush_at": self._last_flush_at,
            }

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _start_flush_thread(self) -> None:
        """Spawn the background flush thread and store a reference to it."""
        t = threading.Thread(target=self._flush_loop, name="apidepth-flush", daemon=True)
        t.start()
        self._flush_thread = t

    def _start_watchdog_thread(self) -> None:
        """Spawn the watchdog thread that monitors and restarts the flush thread."""
        t = threading.Thread(target=self._watchdog_loop, name="apidepth-watchdog", daemon=True)
        t.start()
        self._watchdog_thread = t

    def _flush_loop(self) -> None:
        """Main body of the flush thread: sleep → flush → repeat forever."""
        while True:
            time.sleep(self._flush_interval())
            self._safe_flush()

    def _watchdog_loop(self) -> None:
        """Main body of the watchdog thread.

        Wakes every :data:`WATCHDOG_INTERVAL` seconds.  If the flush thread
        is no longer alive it is restarted and a warning is logged so the
        condition surfaces in error-monitoring tools.
        """
        while True:
            time.sleep(WATCHDOG_INTERVAL)
            if self._flush_thread and not self._flush_thread.is_alive():
                _logger.warning(
                    "[Apidepth] Flush thread died unexpectedly — restarting. "
                    "If this recurs, open an issue with your Python version."
                )
                self._start_flush_thread()

    def _teardown(self) -> None:
        """Close the persistent HTTP connection.

        Called by :meth:`reset` before the singleton is cleared.  Background
        threads are daemon threads and will be garbage-collected naturally;
        there is no need to join them here.
        """
        self._close_conn()

    def _flush_interval(self) -> int:
        """Read the flush interval from the current configuration.

        Falls back to 20 seconds if the configuration is unavailable (e.g.
        during interpreter shutdown when imports may fail).
        """
        try:
            import apidepth
            return apidepth.get_configuration().flush_interval
        except Exception:
            return 20

    # ------------------------------------------------------------------
    # Flush helpers
    # ------------------------------------------------------------------

    def _safe_flush(self) -> None:
        """Drain the queue, send the batch, and update stats.

        Swallows all exceptions so the flush thread never dies due to a
        network error.  Logs a warning after :data:`FAILURE_THRESHOLD`
        consecutive failures to surface persistent problems.
        """
        events = self._drain_queue()
        if not events:
            return
        try:
            self._send_batch(events)
            with self._stats_lock:
                self._consecutive_failures = 0
                self._last_flush_at = time.time()
        except Exception as exc:
            with self._stats_lock:
                self._consecutive_failures += 1
                failures = self._consecutive_failures
            self._invoke_error_callback(exc, len(events), failures)
            if failures >= FAILURE_THRESHOLD:
                _logger.warning(
                    "[Apidepth] Flush has failed %d times consecutively. "
                    "Events are being dropped. Check your API key and network connectivity. "
                    "Last error: %s: %s",
                    failures, type(exc).__name__, exc,
                )

    def _drain_queue(self) -> List[Dict[str, Any]]:
        """Pop up to :data:`MAX_BATCH_SIZE` events from the queue without blocking.

        Returns an empty list when the queue is empty.
        """
        events: List[Dict[str, Any]] = []
        while len(events) < MAX_BATCH_SIZE:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    def _invoke_error_callback(self, exc: Exception, dropped: int, failures: int) -> None:
        """Call ``Configuration.on_flush_error`` if one is configured.

        All exceptions raised inside the callback are swallowed — the
        callback must never crash the flush thread.

        Args:
            exc: The exception that caused the flush to fail.
            dropped: Number of events in the failed batch.
            failures: Current consecutive failure count (after incrementing).
        """
        try:
            import apidepth
            cb = apidepth.get_configuration().on_flush_error
            if cb is not None:
                cb(exc, {
                    "dropped_events": dropped,
                    "consecutive_failures": failures,
                    "total_dropped": self._total_dropped,
                })
        except Exception:
            pass

    # ------------------------------------------------------------------
    # HTTP send
    # ------------------------------------------------------------------

    def _send_batch(self, events: List[Dict[str, Any]]) -> None:
        """Serialise *events* and POST them to the collector endpoint.

        Uses a persistent :class:`http.client.HTTPSConnection` (never the
        monkey-patched *requests* layer) so the collector's own outbound
        traffic is never self-recorded.

        Raises:
            RuntimeError: If the server responds with a non-2xx status.
            ValueError: If the API key contains line-break characters
                (header injection guard).
            Any network-level exception propagated from ``http.client``.

        Note:
            The connection is closed on any exception so the next flush
            attempt always starts with a fresh socket.
        """
        import apidepth
        config = apidepth.get_configuration()
        key = config.api_key or ""

        if not key:
            if not self._warned_no_key:
                self._warned_no_key = True
                _logger.warning(
                    "[Apidepth] No API key configured — events are being dropped. "
                    "Visit www.apidepth.io to create an account and get your key."
                )
            return

        _validate_api_key(key)

        extra = config.extra_vendors or {}
        payload: Dict[str, Any] = {
            "batch": events,
            "sdk": apidepth.sdk_metadata(),
        }
        if extra:
            payload["extra_vendors"] = extra

        body = json.dumps(payload, default=str).encode("utf-8")
        parsed = self._collector_url(config)

        try:
            with self._send_lock:
                conn = self._get_conn(parsed)
                conn.request(
                    "POST",
                    parsed.path or "/",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {key}",
                        "Content-Length": str(len(body)),
                    },
                )
                resp = conn.getresponse()
                resp.read()  # drain the body so the connection can be reused
                if not (200 <= resp.status <= 299):
                    raise RuntimeError(
                        f"Collector returned HTTP {resp.status} — verify your api_key and collector_url"
                    )
        except Exception:
            # Always close on any exception so _get_conn builds a fresh
            # connection on the next flush rather than retrying a broken socket.
            self._close_conn()
            raise

    def _collector_url(self, config: Any) -> ParseResult:
        """Parse and validate the collector URL, memoising the result.

        The URL is resolved once on the first flush.  Changing
        ``Configuration.collector_url`` after the first flush has no effect —
        this is intentional: the URL is a boot-time setting.

        Args:
            config: The current :class:`~apidepth.configuration.Configuration`.

        Returns:
            A :class:`urllib.parse.ParseResult` for the collector endpoint.

        Raises:
            ValueError: If the URL is not HTTPS or resolves to a private address.
        """
        if self._cached_url is None:
            raw = config.collector_url or DEFAULT_URL
            parsed = urlparse(raw)
            _validate_collector_url(parsed)
            self._cached_url = parsed
        return self._cached_url

    def _get_conn(self, parsed: ParseResult) -> http.client.HTTPSConnection:
        """Return a live HTTPS connection to the collector, creating one if needed.

        Must only be called while ``self._send_lock`` is held.

        Args:
            parsed: The parsed collector URL.

        Returns:
            An open :class:`http.client.HTTPSConnection`.
        """
        if self._conn is None:
            ctx = ssl.create_default_context()
            self._conn = http.client.HTTPSConnection(
                parsed.hostname,
                parsed.port or 443,
                timeout=5,
                context=ctx,
            )
        return self._conn

    def _close_conn(self) -> None:
        """Close and discard the persistent HTTP connection.

        Safe to call when ``self._conn`` is already ``None``.
        """
        try:
            if self._conn:
                self._conn.close()
        except Exception:
            pass
        self._conn = None


# ---------------------------------------------------------------------------
# Module-private validators
# ---------------------------------------------------------------------------

def _validate_collector_url(parsed: ParseResult) -> None:
    """Raise ``ValueError`` if *parsed* is not a safe HTTPS collector URL.

    Rejects:
    * Any non-HTTPS scheme (prevents credential exposure over plain HTTP).
    * Loopback, link-local, private RFC-1918, and IPv6 ULA addresses
      (prevents SSRF where the collector is used to probe internal services).
    * Pure-integer hostnames (decimal IP notation, e.g. ``2130706433``
      for ``127.0.0.1``).

    Args:
        parsed: Result of ``urlparse(collector_url)``.

    Raises:
        ValueError: With a descriptive message explaining which constraint
            was violated.
    """
    if parsed.scheme != "https":
        raise ValueError(
            f"Apidepth collector_url must use HTTPS (got {parsed.scheme!r}). "
            "HTTP connections are rejected to prevent SSRF and credential exposure."
        )
    host = (parsed.hostname or "").lower()

    # Expand pure-integer hosts (decimal IP notation, e.g. "2130706433")
    # to dotted-quad form so the private-range regex can match them.
    if re.fullmatch(r"\d+", host):
        n = int(host)
        if 0 < n <= 0xFFFFFFFF:
            host = ".".join(str((n >> s) & 0xFF) for s in (24, 16, 8, 0))

    if not host or _PRIVATE_HOST_RE.search(host):
        raise ValueError(
            "Apidepth collector_url must not target private, loopback, or link-local "
            f"addresses (got {parsed.hostname!r})."
        )


def _validate_api_key(key: str) -> None:
    """Raise ``ValueError`` if *key* contains line-break characters.

    Line-break characters in an HTTP ``Authorization`` header value allow an
    attacker who controls the key value to inject arbitrary headers.  This
    guard catches misconfigured environment variables before the request
    is sent.

    Args:
        key: The raw API key string.

    Raises:
        ValueError: If ``\\r`` or ``\\n`` is found in *key*.
    """
    if "\r" in key or "\n" in key:
        raise ValueError(
            "Apidepth api_key contains illegal line-break characters. "
            "This may indicate header injection — check your APIDEPTH_API_KEY value."
        )
