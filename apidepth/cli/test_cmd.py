"""Implements ``python -m apidepth test``."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError

try:
    from apidepth.version import VERSION
except ImportError:
    VERSION = "unknown"

DEFAULT_COLLECTOR_URL = "https://collector.apidepth.io"
TIMEOUT_SECONDS = 5


def run(argv=None) -> None:
    api_key, collector_url = _load_config()

    if not api_key:
        print("No API key configured.", file=sys.stderr)
        print("Run `python -m apidepth setup` or set APIDEPTH_API_KEY.", file=sys.stderr)
        sys.exit(1)

    base_url = (collector_url or DEFAULT_COLLECTOR_URL).rstrip("/")
    print("Sending test event to collector... ", end="", flush=True)

    try:
        elapsed = _send_test_event(api_key, base_url)
        print(f"✓ received in {elapsed}ms")
        print("Visit your dashboard: https://apidepth.io/dashboard")
    except _TestError as e:
        print("✗")
        print(f"\n{e}", file=sys.stderr)
        if e.hint:
            print(e.hint, file=sys.stderr)
        sys.exit(1)


class _TestError(Exception):
    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.hint = hint


def _load_config():
    try:
        import apidepth

        cfg = apidepth.get_configuration()
        api_key = cfg.api_key or os.environ.get("APIDEPTH_API_KEY")
        collector_url = cfg.collector_url or os.environ.get("APIDEPTH_COLLECTOR_URL")
    except Exception:
        api_key = os.environ.get("APIDEPTH_API_KEY")
        collector_url = os.environ.get("APIDEPTH_COLLECTOR_URL")
    return api_key, collector_url


def _send_test_event(api_key: str, base_url: str) -> int:
    url = f"{base_url}/v1/events"
    payload = json.dumps(
        {
            "batch": [
                {
                    "vendor": "apidepth-test",
                    "endpoint": "/test",
                    "method": "GET",
                    "status": 200,
                    "outcome": "success",
                    "duration_ms": 1,
                    "cold_start": False,
                    "env": "test",
                    "ts": int(time.time() * 1000),
                    "test": True,
                }
            ],
            "sdk": {"name": "apidepth-python", "version": VERSION},
        }
    ).encode()

    req = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    start = time.monotonic()
    try:
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310 — known HTTPS URL
            elapsed = round((time.monotonic() - start) * 1000)
            if resp.status in (200, 201, 204):
                return elapsed
            raise _TestError(
                f"Collector returned HTTP {resp.status}.",
                hint="Check https://status.apidepth.io for service status.",
            )
    except HTTPError as e:
        if e.code in (401, 403):
            raise _TestError(
                f"API key not recognised (HTTP {e.code}).",
                hint="Check the key in your initializer matches your dashboard at https://apidepth.io/dashboard/api-keys",
            ) from e
        raise _TestError(
            f"Collector returned HTTP {e.code}.",
            hint="Check https://status.apidepth.io for service status.",
        ) from e
    except TimeoutError:
        raise _TestError(
            f"No response after {TIMEOUT_SECONDS} seconds.",
            hint="Check for a firewall blocking outbound port 443.",
        )
    except Exception as e:
        msg = str(e).lower()
        if "ssl" in msg or "certificate" in msg:
            raise _TestError(
                f"SSL certificate verification failed: {e}",
                hint="Check your Python SSL configuration.",
            ) from e
        if "connect" in msg or "name or service" in msg:
            raise _TestError(
                f"Could not reach collector: {e}",
                hint="Check outbound HTTPS (port 443) is allowed from this environment.",
            ) from e
        raise _TestError(f"Unexpected error: {e}") from e
