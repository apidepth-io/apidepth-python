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
from urllib.parse import urlparse

_logger = logging.getLogger("apidepth")

MAX_BATCH_SIZE = 100
MAX_QUEUE_SIZE = 5_000
FAILURE_THRESHOLD = 3
WATCHDOG_INTERVAL = 60
DEFAULT_URL = "https://collector.apidepth.io/v1/events"

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
    _instance: Optional["Collector"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "Collector":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance._teardown()
            cls._instance = None

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self._stats_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._consecutive_failures = 0
        self._total_dropped = 0
        self._last_flush_at: Optional[float] = None
        self._conn: Optional[http.client.HTTPSConnection] = None
        self._cached_url: Optional[str] = None
        self._warned_no_key = False
        self._flush_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._start_flush_thread()
        self._start_watchdog_thread()
        atexit.register(self.flush)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, event: Dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._stats_lock:
                self._total_dropped += 1

    def flush(self) -> None:
        """Drain the queue and send synchronously. Called at process exit."""
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
        t = threading.Thread(target=self._flush_loop, name="apidepth-flush", daemon=True)
        t.start()
        self._flush_thread = t

    def _start_watchdog_thread(self) -> None:
        t = threading.Thread(target=self._watchdog_loop, name="apidepth-watchdog", daemon=True)
        t.start()
        self._watchdog_thread = t

    def _flush_loop(self) -> None:
        while True:
            time.sleep(self._flush_interval())
            self._safe_flush()

    def _watchdog_loop(self) -> None:
        while True:
            time.sleep(WATCHDOG_INTERVAL)
            if self._flush_thread and not self._flush_thread.is_alive():
                _logger.warning(
                    "[Apidepth] Flush thread died unexpectedly — restarting. "
                    "If this recurs, open an issue with your Python version."
                )
                self._start_flush_thread()

    def _teardown(self) -> None:
        self._close_conn()

    def _flush_interval(self) -> int:
        try:
            import apidepth
            return apidepth.get_configuration().flush_interval
        except Exception:
            return 20

    # ------------------------------------------------------------------
    # Flush helpers
    # ------------------------------------------------------------------

    def _safe_flush(self) -> None:
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
        events: List[Dict[str, Any]] = []
        while len(events) < MAX_BATCH_SIZE:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    def _invoke_error_callback(self, exc: Exception, dropped: int, failures: int) -> None:
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
            resp.read()  # drain so the connection can be reused
            if not (200 <= resp.status <= 299):
                self._close_conn()
                raise RuntimeError(
                    f"Collector returned HTTP {resp.status} — verify your api_key and collector_url"
                )

    def _collector_url(self, config: Any):
        if self._cached_url is None:
            raw = config.collector_url or DEFAULT_URL
            parsed = urlparse(raw)
            _validate_collector_url(parsed)
            self._cached_url = parsed
        return self._cached_url

    def _get_conn(self, parsed) -> http.client.HTTPSConnection:
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
        try:
            if self._conn:
                self._conn.close()
        except Exception:
            pass
        self._conn = None


def _validate_collector_url(parsed) -> None:
    if parsed.scheme != "https":
        raise ValueError(
            f"Apidepth collector_url must use HTTPS (got {parsed.scheme!r}). "
            "HTTP connections are rejected to prevent SSRF and credential exposure."
        )
    host = (parsed.hostname or "").lower()

    # Expand pure-integer hosts (decimal IP notation)
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
    if "\r" in key or "\n" in key:
        raise ValueError(
            "Apidepth api_key contains illegal line-break characters. "
            "This may indicate header injection — check your APIDEPTH_API_KEY value."
        )
