"""Flask integration for the Apidepth SDK.

Supports both the direct initialisation pattern and the application-factory
pattern (``init_app``)::

    # Direct
    from flask import Flask
    from apidepth.integrations.flask import Apidepth

    app = Flask(__name__)
    app.config["APIDEPTH_API_KEY"] = os.environ["APIDEPTH_API_KEY"]
    app.config["APIDEPTH_ENVIRONMENT"] = "production"
    Apidepth(app)


    # Application factory
    apidepth_ext = Apidepth()

    def create_app():
        app = Flask(__name__)
        app.config["APIDEPTH_API_KEY"] = os.environ["APIDEPTH_API_KEY"]
        apidepth_ext.init_app(app)
        return app

Flask config keys recognised by :meth:`Apidepth.init_app`:

=========================  =======  =========================================
Key                        Type     Notes
=========================  =======  =========================================
``APIDEPTH_API_KEY``       str      **Required.**
``APIDEPTH_ENVIRONMENT``   str      Defaults to ``app.config["ENV"]``.
``APIDEPTH_SAMPLE_RATE``   float    0.0 – 1.0; default 1.0.
``APIDEPTH_FLUSH_INTERVAL``int      Seconds; default 20.
``APIDEPTH_IGNORED_HOSTS`` list     Hostnames to never record.
``APIDEPTH_EXTRA_VENDORS`` dict     ``{vendor_name: hostname}`` pairs.
``APIDEPTH_COLLECTOR_URL`` str      Override the production endpoint.
=========================  =======  =========================================
"""
from __future__ import annotations

import logging
from typing import Any


class Apidepth:
    """Flask extension that bootstraps the Apidepth SDK.

    Follows the standard Flask extension pattern: pass *app* to the
    constructor for simple projects, or call :meth:`init_app` separately
    when using the application-factory pattern.
    """

    # Mapping from Flask config keys to Configuration attribute names.
    # Defined at class level to avoid rebuilding the dict on every init_app call.
    _CONFIG_KEY_MAP = {
        "APIDEPTH_API_KEY":         "api_key",
        "APIDEPTH_COLLECTOR_URL":   "collector_url",
        "APIDEPTH_ENVIRONMENT":     "environment",
        "APIDEPTH_SAMPLE_RATE":     "sample_rate",
        "APIDEPTH_FLUSH_INTERVAL":  "flush_interval",
        "APIDEPTH_IGNORED_HOSTS":   "ignored_hosts",
        "APIDEPTH_EXTRA_VENDORS":   "extra_vendors",
    }

    def __init__(self, app: Any = None) -> None:
        """Initialise the extension, optionally wiring it to *app* immediately.

        Args:
            app: A :class:`flask.Flask` application instance.  When provided,
                :meth:`init_app` is called immediately.  Pass ``None`` (the
                default) when using the application-factory pattern and call
                :meth:`init_app` later.
        """
        self._app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Any) -> None:
        """Wire the Apidepth SDK into *app*.

        Reads ``APIDEPTH_*`` keys from ``app.config``, applies them to the
        configuration singleton, then instruments HTTP clients and starts the
        registry refresh thread.  Mirrors the sequencing of the Ruby gem's
        Railtie ``initializer`` block.

        Args:
            app: A :class:`flask.Flask` application instance.
        """
        import apidepth
        from apidepth import instrumentation, registry_loader
        from apidepth.vendor_registry import VendorRegistry

        config = apidepth.get_configuration()

        # Apply recognised Flask config keys to the configuration singleton.
        for flask_key, attr in self._CONFIG_KEY_MAP.items():
            val = app.config.get(flask_key)
            if val is not None:
                setattr(config, attr, val)

        # Fall back to Flask's own ENV setting if environment was not set explicitly.
        if config.environment is None:
            config.environment = app.config.get("ENV", "development")

        # Route SDK logs through Flask's app logger.
        apidepth.set_logger(logging.getLogger("apidepth"))

        if config.api_key is None:
            app.logger.warning(
                "[Apidepth] No API key configured — events will not be delivered. "
                "Set APIDEPTH_API_KEY in your Flask config."
            )

        instrumentation.instrument()
        VendorRegistry.load_extra_vendors(config.extra_vendors)
        registry_loader.load_and_start()

        app.logger.debug(
            "[Apidepth] Instrumentation active — registry=%s vendors=%d",
            VendorRegistry.version(),
            VendorRegistry.vendor_count(),
        )
