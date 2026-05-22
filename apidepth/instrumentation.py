from __future__ import annotations

import random
import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

# Thread-local flag: set True while the collector is sending to prevent
# the instrumentation from recording its own outbound requests.
_skip = threading.local()

_requests_patched = False
_httpx_patched = False


def instrument() -> None:
    """Monkey-patch installed HTTP clients.

    Safe to call multiple times — subsequent calls are no-ops.
    Patches requests.adapters.HTTPAdapter and httpx.Client/AsyncClient
    when those libraries are installed.
    """
    _patch_requests()
    _patch_httpx()


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------

def _patch_requests() -> None:
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
        if getattr(_skip, "value", False):
            return original(adapter_self, request, stream=stream, timeout=timeout,
                            verify=verify, cert=cert, proxies=proxies)

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
            _record_event_requests(request, response, duration_ms, host, parsed.path or "/")
            return response
        except Exception as exc:
            duration_ms = _elapsed_ms(start)
            _record_timeout_requests(request, exc, duration_ms, host, parsed.path or "/")
            raise

    requests.adapters.HTTPAdapter.send = _patched_send  # type: ignore[method-assign]
    _requests_patched = True


def _record_event_requests(request: Any, response: Any, duration_ms: int,
                           host: str, path: str) -> None:
    try:
        from apidepth.vendor_registry import VendorRegistry
        result = VendorRegistry.identify(host, path)
        if result is None:
            return
        vendor, endpoint = result

        status = response.status_code
        outcome = _outcome_from_status(status)

        now_ms = _now_ms()
        from apidepth.rate_limit_headers import extract as extract_rl
        rl = extract_rl(dict(response.headers), now_ms)

        import apidepth
        from apidepth import collector, event
        collector.Collector.instance().record(event.build({
            "vendor": vendor,
            "endpoint": endpoint,
            "method": request.method.upper(),
            "status": status,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "cold_start": False,
            "env": _resolve_env(),
            "ts": now_ms,
            **(rl or {}),
        }))
    except Exception:
        pass


def _record_timeout_requests(request: Any, exc: Exception, duration_ms: int,
                              host: str, path: str) -> None:
    try:
        import requests.exceptions
        if not isinstance(exc, (requests.exceptions.Timeout,
                                requests.exceptions.ConnectTimeout,
                                requests.exceptions.ReadTimeout)):
            return

        from apidepth.vendor_registry import VendorRegistry
        result = VendorRegistry.identify(host, path)
        if result is None:
            return
        vendor, endpoint = result

        import apidepth
        from apidepth import collector, event
        collector.Collector.instance().record(event.build({
            "vendor": vendor,
            "endpoint": endpoint,
            "method": request.method.upper(),
            "status": None,
            "outcome": "timeout",
            "error_class": type(exc).__name__,
            "duration_ms": duration_ms,
            "cold_start": False,
            "env": _resolve_env(),
            "ts": _now_ms(),
        }))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# httpx
# ---------------------------------------------------------------------------

def _patch_httpx() -> None:
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
        if getattr(_skip, "value", False):
            return original_sync(client_self, request, **kwargs)

        import apidepth
        config = apidepth.get_configuration()
        host = str(request.url.host)

        if not config.enabled or host in config.ignored_hosts or not _sampled(config):
            return original_sync(client_self, request, **kwargs)

        start = time.monotonic()
        try:
            response = original_sync(client_self, request, **kwargs)
            duration_ms = _elapsed_ms(start)
            _record_event_httpx(request, response, duration_ms, host, str(request.url.path))
            return response
        except Exception as exc:
            duration_ms = _elapsed_ms(start)
            _record_timeout_httpx(request, exc, duration_ms, host, str(request.url.path))
            raise

    async def _patched_async_send(client_self, request, **kwargs):
        if getattr(_skip, "value", False):
            return await original_async(client_self, request, **kwargs)

        import apidepth
        config = apidepth.get_configuration()
        host = str(request.url.host)

        if not config.enabled or host in config.ignored_hosts or not _sampled(config):
            return await original_async(client_self, request, **kwargs)

        start = time.monotonic()
        try:
            response = await original_async(client_self, request, **kwargs)
            duration_ms = _elapsed_ms(start)
            _record_event_httpx(request, response, duration_ms, host, str(request.url.path))
            return response
        except Exception as exc:
            duration_ms = _elapsed_ms(start)
            _record_timeout_httpx(request, exc, duration_ms, host, str(request.url.path))
            raise

    httpx.Client.send = _patched_sync_send  # type: ignore[method-assign]
    httpx.AsyncClient.send = _patched_async_send  # type: ignore[method-assign]
    _httpx_patched = True


def _record_event_httpx(request: Any, response: Any, duration_ms: int,
                        host: str, path: str) -> None:
    try:
        from apidepth.vendor_registry import VendorRegistry
        result = VendorRegistry.identify(host, path)
        if result is None:
            return
        vendor, endpoint = result

        status = response.status_code
        outcome = _outcome_from_status(status)
        now_ms = _now_ms()

        from apidepth.rate_limit_headers import extract as extract_rl
        rl = extract_rl(dict(response.headers), now_ms)

        from apidepth import collector, event
        collector.Collector.instance().record(event.build({
            "vendor": vendor,
            "endpoint": endpoint,
            "method": request.method.upper(),
            "status": status,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "cold_start": False,
            "env": _resolve_env(),
            "ts": now_ms,
            **(rl or {}),
        }))
    except Exception:
        pass


def _record_timeout_httpx(request: Any, exc: Exception, duration_ms: int,
                          host: str, path: str) -> None:
    try:
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
            "method": request.method.upper(),
            "status": None,
            "outcome": "timeout",
            "error_class": type(exc).__name__,
            "duration_ms": duration_ms,
            "cold_start": False,
            "env": _resolve_env(),
            "ts": _now_ms(),
        }))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _elapsed_ms(start: float) -> int:
    return round((time.monotonic() - start) * 1000)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _outcome_from_status(status: Optional[int]) -> str:
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
    rate = config.sample_rate
    return rate >= 1.0 or random.random() < rate


def _resolve_env() -> str:
    try:
        import apidepth
        return apidepth.get_configuration().environment or "unknown"
    except Exception:
        return "unknown"
