"""Apidepth Python SDK.

Minimal setup for non-framework Python scripts::

    import apidepth

    apidepth.configure(
        api_key="apd_live_...",
        environment="production",
    )
    apidepth.instrument()   # patches requests and httpx if installed

For Django, add 'apidepth.integrations.django' to INSTALLED_APPS instead.
For Flask, use apidepth.integrations.flask.Apidepth(app).
"""
from __future__ import annotations

import logging
import platform
import sys
from typing import Any, Dict, Optional

from apidepth.version import VERSION
from apidepth.configuration import Configuration

__version__ = VERSION

_configuration: Optional[Configuration] = None
_logger: Optional[logging.Logger] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure(**kwargs: Any) -> Configuration:
    """Set configuration options. Call before the first outbound HTTP request.

    Accepts the same keyword arguments as Configuration attributes:
    api_key, environment, sample_rate, ignored_hosts, extra_vendors, etc.
    """
    config = get_configuration()
    for key, value in kwargs.items():
        setattr(config, key, value)
    return config


def get_configuration() -> Configuration:
    global _configuration
    if _configuration is None:
        _configuration = Configuration()
    return _configuration


def get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger("apidepth")
    return _logger


def set_logger(logger: logging.Logger) -> None:
    global _logger
    _logger = logger


def instrument() -> None:
    """Patch installed HTTP clients (requests, httpx).

    Safe to call multiple times. Call this once at application startup after
    configure(). Framework integrations (Django, Flask) call this automatically.
    """
    from apidepth.instrumentation import instrument as _instrument
    _instrument()


def sdk_metadata() -> Dict[str, Any]:
    """Return a metadata dict included in every batch payload."""
    return {
        "name": "apidepth-python",
        "version": VERSION,
        "python_version": sys.version.split()[0],
        "python_platform": platform.platform(),
    }


def sanitize_log(s: str) -> str:
    """Strip line-break characters from untrusted strings before they reach log output."""
    return str(s).translate(str.maketrans("\r\n\t", "   "))[:200]
