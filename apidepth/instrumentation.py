"""HTTP client instrumentation for the Apidepth SDK.

Monkey-patching strategy
------------------------
The SDK intercepts outbound HTTP requests by replacing the ``send`` method
on the adapter/transport layer of each supported library.  This is equivalent
to Ruby's ``Module#prepend`` on ``Net::HTTP``.

Supported libraries (detected at runtime; each is optional):

* **requests** — ``requests.adapters.HTTPAdapter.send`` is replaced.
  All ``Session``-based and top-level ``requests.get / .post / …`` calls
  go through this path.

* **httpx** — ``httpx.Client.send`` *and* ``httpx.AsyncClient.send`` are
  replaced so both sync and async usage is covered.

Recursion guard
---------------
The :class:`~apidepth.collector.Collector` uses ``http.client`` directly
(never *requests* or *httpx*) so the patched methods are never on its call
path.  Recursion is prevented architecturally, not by a flag.

Idempotency
-----------
:func:`instrument` is safe to call multiple times — a module-level boolean
per library prevents double-patching.

Cold-start detection
--------------------
The Ruby gem detects cold starts via ``Net::HTTP#started?``: it tags the
**first** request on a fresh connection with ``cold_start: true`` so the
Apidepth collector can exclude DNS + TCP + TLS handshake overhead from
latency percentile calculations (p50 / p95 / p99).

Neither *requests* nor *httpx* exposes a public API for inspecting whether
the underlying socket is a reused keep-alive connection.  Instead, the Python
SDK uses a per-process host registry: the **first request to each hostname**
within a process lifetime is tagged ``cold_start: True``.  This accurately
captures the highest-impact scenario (DNS + TCP + TLS overhead) without
touching private library internals.

The registry is cleared after ``os.fork()`` so each forked worker (gunicorn,
uWSGI) correctly marks its own first requests as cold.

Known limitation: mid-process connection re-establishment after a keep-alive
timeout is not detected — only the first request per host per process is
tagged.  This is a minor gap for long-lived, low-throughput services; for
serverless and short-lived workers the behaviour is identical to the Ruby gem.
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

_requests_patched = False
_httpx_patched = False
_fork_safety_registered = False
_cold_start_hosts: set = set()


def instrument() -> None:
    """Monkey-patch all installed HTTP client libraries.

    Patches *requests* and *httpx* if they are importable.  Libraries that
    are not installed are silently skipped.  Safe to call multiple times —
    subsequent calls after the first are no-ops.

    Also registers fork-safety (``os.register_at_fork``) for gunicorn/uWSGI
    workers on the first call so that forked child processes each get a fresh
    :class:`~apidepth.collector.Collector` with its own flush thread.

    Call this once at application startup **after** :func:`apidepth.configure`.
    Framework integrations (Django, Flask) call it automatically inside their
    boot hooks.
    """
    global _fork_safety_registered
    if not _fork_safety_registered:
        from apidepth.collector import Collector

        Collector.register_fork_safety()
        try:
            import os as _os

            _os.register_at_fork(after_in_child=_clear_cold_start_hosts)
        except AttributeError:
            pass  # Windows — os.register_at_fork is POSIX-only
        _fork_safety_registered = True
    _patch_requests()
    _patch_httpx()


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------


def _patch_requests() -> None:
    """Replace ``requests.adapters.HTTPAdapter.send`` with the instrumented version.

    Wraps the original method in a closure so the original is always
    reachable even if the attribute is later replaced by other middleware.
    Sets ``_requests_patched = True`` only after a successful replacement.
    """
    global _requests_patched
    if _requests_patched:
        return
    try:
        import requests.adapters
    except ImportError:
        return

    original = requests.adapters.HTTPAdapter.send

    def _patched_send(adapter_self, request, **kwargs):
        """Instrumented replacement for ``HTTPAdapter.send``.

        Early-exit conditions (evaluated cheapest-first):

        1. Recursion guard — we are inside our own collector flush.
        2. SDK disabled via ``Configuration.enabled``.
        3. Host is on ``Configuration.ignored_hosts``.
        4. Probabilistic sampling — request not selected this tick.
        """
        import apidepth

        config = apidepth.get_configuration()

        if not config.enabled:
            return original(adapter_self, request, **kwargs)

        parsed = urlparse(request.url)
        host = parsed.hostname or ""

        if config.ignored_host(host):
            return original(adapter_self, request, **kwargs)

        if not _sampled(config):
            return original(adapter_self, request, **kwargs)

        cold_start = _is_cold_start(host)
        start = time.monotonic()
        try:
            response = original(adapter_self, request, **kwargs)
            duration_ms = _elapsed_ms(start)
            _record_success(
                method=request.method.upper(),
                host=host,
                path=parsed.path or "/",
                status=response.status_code,
                headers={k.lower(): v for k, v in response.headers.items()},
                duration_ms=duration_ms,
                cold_start=cold_start,
                response=response,
            )
            return response
        except Exception as exc:
            duration_ms = _elapsed_ms(start)
            _record_timeout_if_applicable(
                exc=exc,
                method=request.method.upper(),
                host=host,
                path=parsed.path or "/",
                duration_ms=duration_ms,
                cold_start=cold_start,
                is_requests_timeout=True,
            )
            raise

    requests.adapters.HTTPAdapter.send = _patched_send  # type: ignore[method-assign]
    _requests_patched = True


# ---------------------------------------------------------------------------
# httpx
# ---------------------------------------------------------------------------


def _patch_httpx() -> None:
    """Replace ``httpx.Client.send`` and ``httpx.AsyncClient.send``.

    Both sync and async clients share the same recording helpers so the
    logic is not duplicated.  The async replacement is a native ``async def``
    so it can be awaited correctly.
    """
    global _httpx_patched
    if _httpx_patched:
        return
    try:
        import httpx
    except ImportError:
        return

    original_sync = httpx.Client.send
    original_async = httpx.AsyncClient.send

    def _patched_sync_send(client_self, request, **kwargs):
        """Instrumented replacement for ``httpx.Client.send`` (sync)."""
        import apidepth

        config = apidepth.get_configuration()
        host = str(request.url.host)

        if not config.enabled or config.ignored_host(host) or not _sampled(config):
            return original_sync(client_self, request, **kwargs)

        cold_start = _is_cold_start(host)
        start = time.monotonic()
        try:
            response = original_sync(client_self, request, **kwargs)
            duration_ms = _elapsed_ms(start)
            _record_success(
                method=request.method.upper(),
                host=host,
                path=str(request.url.path),
                status=response.status_code,
                headers=dict(response.headers),
                duration_ms=duration_ms,
                cold_start=cold_start,
                response=response,
            )
            return response
        except Exception as exc:
            duration_ms = _elapsed_ms(start)
            _record_timeout_if_applicable(
                exc=exc,
                method=request.method.upper(),
                host=host,
                path=str(request.url.path),
                duration_ms=duration_ms,
                cold_start=cold_start,
                is_requests_timeout=False,
            )
            raise

    async def _patched_async_send(client_self, request, **kwargs):
        """Instrumented replacement for ``httpx.AsyncClient.send`` (async)."""
        import apidepth

        config = apidepth.get_configuration()
        host = str(request.url.host)

        if not config.enabled or config.ignored_host(host) or not _sampled(config):
            return await original_async(client_self, request, **kwargs)

        cold_start = _is_cold_start(host)
        start = time.monotonic()
        try:
            response = await original_async(client_self, request, **kwargs)
            duration_ms = _elapsed_ms(start)
            _record_success(
                method=request.method.upper(),
                host=host,
                path=str(request.url.path),
                status=response.status_code,
                headers=dict(response.headers),
                duration_ms=duration_ms,
                cold_start=cold_start,
                response=response,
            )
            return response
        except Exception as exc:
            duration_ms = _elapsed_ms(start)
            _record_timeout_if_applicable(
                exc=exc,
                method=request.method.upper(),
                host=host,
                path=str(request.url.path),
                duration_ms=duration_ms,
                cold_start=cold_start,
                is_requests_timeout=False,
            )
            raise

    httpx.Client.send = _patched_sync_send  # type: ignore[method-assign]
    httpx.AsyncClient.send = _patched_async_send  # type: ignore[method-assign]
    _httpx_patched = True


# ---------------------------------------------------------------------------
# Shared recording helpers
# ---------------------------------------------------------------------------


def _record_success(
    *,
    method: str,
    host: str,
    path: str,
    status: int,
    headers: Dict[str, str],
    duration_ms: int,
    cold_start: bool,
    response: Any = None,
) -> None:
    """Build and enqueue a successful-response event.

    Identifies the vendor from *host*, normalises *path*, extracts any
    rate-limit headers, then hands the assembled event dict to
    :meth:`~apidepth.collector.Collector.record`.

    All exceptions are swallowed so instrumentation can never crash the
    caller's code path.

    Args:
        method: Uppercase HTTP method (e.g. ``"POST"``).
        host: Bare hostname of the request (e.g. ``"api.stripe.com"``).
        path: Request path, query string already stripped by the caller.
        status: HTTP response status code.
        headers: Response headers as a plain ``dict``.
        duration_ms: Wall-clock request duration in milliseconds.
    """
    try:
        from apidepth.vendor_registry import VendorRegistry

        result = VendorRegistry.identify(host, path)
        if result is None:
            return
        vendor, endpoint = result

        outcome = _outcome_from_status(status)
        now_ms = _now_ms()

        from apidepth.rate_limit_headers import extract as extract_rl
        from apidepth.model_name_extractor import extract as extract_model

        rl = extract_rl(headers, now_ms)
        model_name = extract_model(host, response) if response is not None else None

        attrs: Dict[str, Any] = {
            "vendor": vendor,
            "endpoint": endpoint,
            "method": method,
            "status": status,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "cold_start": cold_start,
            "env": _resolve_env(),
            "ts": now_ms,
            **(rl or {}),
        }
        if model_name:
            attrs["model_name"] = model_name

        from apidepth import collector, event

        collector.Collector.instance().record(event.build(attrs))
    except Exception:
        pass


def _record_timeout_if_applicable(
    *,
    exc: Exception,
    method: str,
    host: str,
    path: str,
    duration_ms: int,
    cold_start: bool,
    is_requests_timeout: bool,
) -> None:
    """Build and enqueue a timeout event if *exc* is a recognised timeout type.

    Timeouts are a leading indicator of vendor degradation — they appear
    before the vendor acknowledges an incident.  The event is always
    re-raised by the caller so the application's own error handling is
    unaffected.

    Args:
        exc: The exception that was raised by the HTTP client.
        method: Uppercase HTTP method.
        host: Bare hostname of the request.
        path: Request path.
        duration_ms: Wall-clock request duration at the time of the exception.
        is_requests_timeout: ``True`` when called from the *requests* patch;
            ``False`` when called from the *httpx* patch.  Controls which
            exception classes are considered timeouts.
    """
    try:
        if is_requests_timeout:
            import requests.exceptions

            if not isinstance(
                exc,
                (
                    requests.exceptions.Timeout,
                    requests.exceptions.ConnectTimeout,
                    requests.exceptions.ReadTimeout,
                ),
            ):
                return
        else:
            import httpx

            if not isinstance(exc, httpx.TimeoutException):
                return

        from apidepth.vendor_registry import VendorRegistry

        result = VendorRegistry.identify(host, path)
        if result is None:
            return
        vendor, endpoint = result

        from apidepth import collector, event

        collector.Collector.instance().record(
            event.build(
                {
                    "vendor": vendor,
                    "endpoint": endpoint,
                    "method": method,
                    "status": None,
                    "outcome": "timeout",
                    "error_class": type(exc).__name__,
                    "duration_ms": duration_ms,
                    "cold_start": cold_start,
                    "env": _resolve_env(),
                    "ts": _now_ms(),
                }
            )
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Shared utility helpers
# ---------------------------------------------------------------------------


def _is_cold_start(host: str) -> bool:
    """Return ``True`` if this is the first request to *host* in this process.

    Adds *host* to the registry on first call, so subsequent calls for the
    same host return ``False``.
    """
    global _cold_start_hosts
    if host in _cold_start_hosts:
        return False
    _cold_start_hosts.add(host)
    return True


def _clear_cold_start_hosts() -> None:
    """Reset the cold-start host registry.  Called after ``os.fork()`` so each
    worker process marks its own first requests as cold.
    """
    global _cold_start_hosts
    _cold_start_hosts = set()


def _elapsed_ms(start: float) -> int:
    """Return milliseconds elapsed since *start* (a ``time.monotonic()`` value)."""
    return round((time.monotonic() - start) * 1000)


def _now_ms() -> int:
    """Return the current epoch time in milliseconds."""
    return int(time.time() * 1000)


def _outcome_from_status(status: Optional[int]) -> str:
    """Map an HTTP status code to an Apidepth outcome string.

    Args:
        status: HTTP status code, or ``None`` for timeout events.

    Returns:
        One of ``"success"``, ``"client_error"``, ``"server_error"``, or
        ``"unknown"``.
    """
    if status is None:
        return "unknown"
    if 200 <= status <= 299:
        return "success"
    if 400 <= status <= 499:
        return "client_error"
    if 500 <= status <= 599:
        return "server_error"
    return "unknown"


def _sampled(config: Any) -> bool:
    """Return ``True`` if this request should be recorded given the sample rate.

    At ``sample_rate = 1.0`` (default) this always returns ``True`` without
    calling ``random.random()``.

    Args:
        config: The current :class:`~apidepth.configuration.Configuration`.
    """
    rate = config.sample_rate
    return rate >= 1.0 or random.random() < rate


def _resolve_env() -> str:
    """Return the configured deployment environment tag, defaulting to ``"unknown"``."""
    try:
        import apidepth

        return apidepth.get_configuration().environment or "unknown"
    except Exception:
        return "unknown"
