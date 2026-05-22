from __future__ import annotations

import http.client
import json
import logging
import os
import ssl
import threading
from typing import Any, Dict, Optional
from urllib.parse import urlparse

_logger = logging.getLogger("apidepth")

REGISTRY_URL = "https://collector.apidepth.io/v1/registry"
MAX_RESPONSE_BYTES = 512_000


def load_and_start() -> None:
    """Fetch the best available registry and start the background refresh thread.

    Priority: remote → disk cache → bundled baseline (already loaded at import).
    """
    from apidepth.vendor_registry import VendorRegistry
    import apidepth

    config = apidepth.get_configuration()
    registry = _fetch_remote(config) or _load_from_disk(config)
    if registry:
        VendorRegistry.replace(registry, config.extra_vendors or {})

    _start_refresh_thread(config)


def _start_refresh_thread(config: Any) -> threading.Thread:
    def _loop() -> None:
        while True:
            import apidepth
            cfg = apidepth.get_configuration()
            interval = cfg.registry_refresh_interval
            threading.Event().wait(interval)
            registry = _fetch_remote(cfg)
            if registry:
                from apidepth.vendor_registry import VendorRegistry
                VendorRegistry.replace(registry, cfg.extra_vendors or {})

    t = threading.Thread(target=_loop, name="apidepth-registry", daemon=True)
    t.start()
    return t


def _fetch_remote(config: Any) -> Optional[Dict[str, Any]]:
    try:
        parsed = urlparse(REGISTRY_URL)
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=5, context=ctx)
        conn.request("GET", parsed.path, headers={"Authorization": f"Bearer {config.api_key or ''}"})
        resp = conn.getresponse()
        if resp.status != 200:
            return None

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
            conn.close()  # type: ignore[union-attr]
        except Exception:
            pass


def _load_from_disk(config: Any) -> Optional[Dict[str, Any]]:
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
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"registry_cache_path must be an absolute path (got {path!r})")
    if ".." in path.split("/"):
        raise ValueError(f"registry_cache_path must not contain '..' traversal segments (got {path!r})")


def _sanitize(s: str) -> str:
    return str(s).translate(str.maketrans("\r\n\t", "   "))[:200]
