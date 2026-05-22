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

Known divergence from the Ruby gem
------------------------------------
The Ruby gem detects cold starts via ``Net::HTTP#started?``: it tags the
**first** request on a fresh connection with ``cold_start: true`` so the
Apidepth collector can exclude DNS + TCP + TLS handshake overhead from
latency percentile calculations (p50 / p95 / p99).

Neither *requests* (backed by urllib3's ``PoolManager``) nor *httpx* exposes
a public API for inspecting whether the underlying socket is a reused
keep-alive connection.  Accessing private internals would be fragile across
library versions and minor releases, so the Python SDK always sends
``cold_start: False``.

Impact by traffic pattern:

* **High-throughput web services** — Negligible.  Cold starts are a tiny
  fraction of total requests; percentile inflation is unmeasurable.
* **Low-throughput services / cron jobs** — Noticeable.  The first request
  in each run pays DNS + TCP + TLS overhead (~50–200 ms extra) but is not
  excluded from percentile calculations.  p95/p99 may read slightly worse
  than the Ruby-instrumented equivalent.
* **Serverless / short-lived workers** — Material.  Every invocation starts
  cold; all latency data includes connection overhead.  Absolute durations
  are still accurate; comparisons against Ruby-instrumented services will
  show the Python side as systematically higher.

If cold-start exclusion is important for your environment, instrument a
custom metric (e.g. first-request flag set in a thread-local) and filter
those events in your dashboard until the underlying libraries expose the
required connection-state API.
"""
from __future__ import annotations

import random
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

_requests_patched = False
_httpx_patched = False


def instrument() -> None:
    """Monkey-patch all installed HTTP client libraries.

    Patches *requests* and *httpx* if they are importable.  Libraries that
    are not installed are silently skipped.  Safe to call multiple times —
    subsequent calls after the first are no-ops.

    Call this once at application startup **after** :func:`apidepth.configure`.
    Framework integrations (Django, Flask) call it automatically inside their
    boot hooks.
    """
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

    def _patched_send(adapter_self, request, stream=False, timeout=None,
                      verify=True, cert=None, proxies=None):
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
            return original(adapter_self, request, stream=stream, timeout=timeout,
                            verify=verify, cert=cert, proxies=proxies)

        parsed = urlparse(request.url)
        host = parsed.hostname or ""

        if host in config.ignored_hosts:
            return original(adapter_self, request, stream=stream, timeout=timeout,
                            verify=verify, cert=cert, proxies=proxies)

        if not _sampled(config):
            return original(adapter_self, request, stream=stream, timeout=timeout,
                            verify=verify, cert=cert, proxies=proxies)

        start = time.monotonic()
        try:
            response = original(adapter_self, request, stream=stream, timeout=timeout,
                                verify=verify, cert=cert, proxies=proxies)
            duration_ms = _elapsed_ms(start)
            _record_success(
                method=request.method.upper(),
                host=host,
                path=parsed.path or "/",
                status=response.status_code,
                headers=dict(response.headers),
                duration_ms=duration_ms,
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

        if not config.enabled or host in config.ignored_hosts or not _sampled(config):
            return original_sync(client_self, request, **kwargs)

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
                is_requests_timeout=False,
            )
            raise

    async def _patched_async_send(client_self, request, **kwargs):
        """Instrumented replacement for ``httpx.AsyncClient.send`` (async)."""
        import apidepth
        config = apidepth.get_configuration()
        host = str(request.url.host)

        if not config.enabled or host in config.ignored_hosts or not _sampled(config):
            return await original_async(client_self, request, **kwargs)

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
        rl = extract_rl(headers, now_ms)

        from apidepth import collector, event
        collector.Collector.instance().record(event.build({
            "vendor": vendor,
            "endpoint": endpoint,
            "method": method,
            "status": status,
            "outcome": outcome,
            "duration_ms": duration_ms,
            # Always False — neither requests nor httpx exposes a public API to
            # detect keep-alive connection reuse (Ruby uses Net::HTTP#started?).
            # See the module docstring for impact details.
            "cold_start": False,
            "env": _resolve_env(),
            "ts": now_ms,
            **(rl or {}),
        }))
    except Exception:
        pass


def _record_timeout_if_applicable(
    *,
    exc: Exception,
    method: str,
    host: str,
    path: str,
    duration_ms: int,
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
            if not isinstance(exc, (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
            )):
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
        collector.Collector.instance().record(event.build({
            "vendor": vendor,
            "endpoint": endpoint,
            "method": method,
            "status": None,
            "outcome": "timeout",
            "error_class": type(exc).__name__,
            "duration_ms": duration_ms,
            # Always False — see module docstring for the cold_start limitation.
            "cold_start": False,
            "env": _resolve_env(),
            "ts": _now_ms(),
        }))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Shared utility helpers
# ---------------------------------------------------------------------------

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
