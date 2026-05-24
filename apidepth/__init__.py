"""Apidepth Python SDK.

Tracks outbound API latency, error rates, and rate-limit quota across
third-party vendors — Stripe, OpenAI, Anthropic, Twilio, GitHub, and more.
Requires zero changes to existing HTTP call sites.

Quick start (non-framework)::

    import apidepth

    apidepth.configure(
        api_key="apd_live_...",
        environment="production",
    )
    apidepth.instrument()   # patches requests and httpx if installed

For **Django**, add ``"apidepth.integrations.django"`` to ``INSTALLED_APPS``
and set ``APIDEPTH = {"api_key": ...}`` in ``settings.py``.

For **Flask**, use ``apidepth.integrations.flask.Apidepth(app)``.

Module-level globals
--------------------
``_configuration``
    Lazily created :class:`~apidepth.configuration.Configuration` singleton.
    Always access via :func:`get_configuration`.

``_logger``
    The ``logging.Logger`` used by all SDK components.  Defaults to the
    standard ``"apidepth"`` logger.  Override with :func:`set_logger` to
    route SDK log output through a framework logger (Django, Flask).
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
_configuration_lock = __import__("threading").Lock()
_logger: Optional[logging.Logger] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def configure(**kwargs: Any) -> Configuration:
    """Set one or more configuration options and return the singleton.

    Must be called before :func:`instrument` and before any instrumented HTTP
    requests are made.  Framework integrations call this automatically inside
    their boot hooks, so manual calls are only needed for non-framework use.

    Args:
        **kwargs: Any attribute defined on
            :class:`~apidepth.configuration.Configuration`.  Common options::

                apidepth.configure(
                    api_key=os.environ["APIDEPTH_API_KEY"],
                    environment="production",
                    sample_rate=0.5,
                    ignored_hosts=["internal.example.com"],
                )

    Returns:
        The updated :class:`~apidepth.configuration.Configuration` singleton.

    Raises:
        TypeError: If an unknown configuration key is passed.
    """
    from apidepth.configuration import Configuration as _Cfg

    unknown = set(kwargs) - _Cfg.VALID_KEYS
    if unknown:
        raise TypeError(
            f"apidepth.configure() got unexpected keyword argument(s): "
            f"{', '.join(sorted(unknown))}. "
            f"Valid options: {', '.join(sorted(_Cfg.VALID_KEYS))}."
        )
    config = get_configuration()
    for key, value in kwargs.items():
        setattr(config, key, value)
    return config


def get_configuration() -> Configuration:
    """Return the process-wide :class:`~apidepth.configuration.Configuration` singleton.

    Creates the singleton with default values on the first call.  Thread-safe.
    """
    global _configuration
    if _configuration is None:
        with _configuration_lock:
            if _configuration is None:
                _configuration = Configuration()
    return _configuration


def get_logger() -> logging.Logger:
    """Return the SDK logger, creating the default one if none has been set.

    The default logger is ``logging.getLogger("apidepth")``.  Framework
    integrations replace it with the framework's own logger via
    :func:`set_logger` so all SDK output flows through the framework's
    logging pipeline.
    """
    global _logger
    if _logger is None:
        _logger = logging.getLogger("apidepth")
    return _logger


def set_logger(logger: logging.Logger) -> None:
    """Replace the SDK logger.

    Call this inside a framework boot hook before any SDK log output is
    produced.  After this call all SDK modules that use
    ``logging.getLogger("apidepth")`` will pick up the new logger because
    the Python logging system resolves loggers by name at emit time.

    Args:
        logger: The replacement :class:`logging.Logger`.
    """
    global _logger
    _logger = logger


def instrument() -> None:
    """Patch installed HTTP client libraries (*requests*, *httpx*).

    Safe to call multiple times — subsequent calls after the first are
    no-ops.  Must be called after :func:`configure`.  Framework integrations
    (Django, Flask) call this automatically.

    After this call, all outbound HTTP requests made through *requests* or
    *httpx* to recognised vendor hostnames are automatically captured and
    enqueued for the next batch flush.
    """
    from apidepth.instrumentation import instrument as _instrument

    _instrument()


def sdk_metadata() -> Dict[str, Any]:
    """Return a metadata dict included in every batch payload.

    The metadata lets the collector correlate data-quality issues with
    specific SDK versions, Python runtimes, and app servers without
    requiring a support ticket.  Mirrors the Ruby gem's ``sdk_metadata``
    which includes ``rails_version`` and ``app_server``.

    Returns:
        A dict with keys ``name``, ``version``, ``python_version``,
        ``python_platform``, and ``app_server``.
    """
    return {
        "name": "apidepth-python",
        "version": VERSION,
        "python_version": sys.version.split()[0],
        "python_platform": platform.platform(),
        "app_server": _detect_app_server(),
    }


def _detect_app_server() -> str:
    """Detect the WSGI/ASGI server by inspecting already-loaded modules.

    Uses ``sys.modules`` rather than attempting new imports so that the
    detection is zero-cost and does not force-load server libraries as a
    side-effect.  This mirrors the Ruby gem's ``detect_app_server`` which
    uses ``defined?(Puma)`` / ``defined?(Unicorn)`` / ``defined?(PhusionPassenger)``.

    Servers checked (in priority order):

    * ``gunicorn``   — most common WSGI server
    * ``uwsgi``      — uWSGI (the ``uwsgi`` C extension module)
    * ``waitress``   — pure-Python WSGI server
    * ``uvicorn``    — ASGI server (FastAPI, Starlette)
    * ``hypercorn``  — ASGI server
    * ``daphne``     — ASGI server (Django Channels)

    Returns:
        The lowercase server name, or ``"unknown"`` if none is detected.
    """
    mods = sys.modules
    if "gunicorn" in mods:
        return "gunicorn"
    if "uwsgi" in mods:
        return "uwsgi"
    if "waitress" in mods:
        return "waitress"
    if "uvicorn" in mods:
        return "uvicorn"
    if "hypercorn" in mods:
        return "hypercorn"
    if "daphne" in mods:
        return "daphne"
    return "unknown"


def sanitize_log(s: str) -> str:
    """Strip CR, LF, and TAB from *s* and truncate to 200 characters.

    Used throughout the SDK before interpolating untrusted strings (vendor
    names, error messages, hostnames from registry data) into log output.
    Prevents log-injection attacks (CVE-2025-27111 class).

    Args:
        s: Any string value.  Non-string inputs are coerced via ``str()``.

    Returns:
        The sanitised, truncated string.
    """
    return str(s).translate(str.maketrans("\r\n\t", "   "))[:200]
