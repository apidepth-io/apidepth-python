"""Flask integration for Apidepth.

Usage::

    from flask import Flask
    from apidepth.integrations.flask import Apidepth

    app = Flask(__name__)
    app.config["APIDEPTH_API_KEY"] = os.environ["APIDEPTH_API_KEY"]
    app.config["APIDEPTH_ENVIRONMENT"] = "production"

    Apidepth(app)

Or with the application factory pattern::

    apidepth_ext = Apidepth()

    def create_app():
        app = Flask(__name__)
        apidepth_ext.init_app(app)
        return app

Flask config keys (all optional except APIDEPTH_API_KEY):

    APIDEPTH_API_KEY            str
    APIDEPTH_ENVIRONMENT        str        default: "development"
    APIDEPTH_SAMPLE_RATE        float      default: 1.0
    APIDEPTH_FLUSH_INTERVAL     int        default: 20
    APIDEPTH_IGNORED_HOSTS      list[str]  default: []
    APIDEPTH_EXTRA_VENDORS      dict       default: {}
    APIDEPTH_COLLECTOR_URL      str        default: production endpoint
"""
from __future__ import annotations

import logging
from typing import Any, Optional


class Apidepth:
    def __init__(self, app: Any = None) -> None:
        self._app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Any) -> None:
        import apidepth
        from apidepth import instrumentation, registry_loader
        from apidepth.vendor_registry import VendorRegistry

        config = apidepth.get_configuration()

        _KEYS = {
            "APIDEPTH_API_KEY":         "api_key",
            "APIDEPTH_COLLECTOR_URL":   "collector_url",
            "APIDEPTH_ENVIRONMENT":     "environment",
            "APIDEPTH_SAMPLE_RATE":     "sample_rate",
            "APIDEPTH_FLUSH_INTERVAL":  "flush_interval",
            "APIDEPTH_IGNORED_HOSTS":   "ignored_hosts",
            "APIDEPTH_EXTRA_VENDORS":   "extra_vendors",
        }
        for flask_key, attr in _KEYS.items():
            val = app.config.get(flask_key)
            if val is not None:
                setattr(config, attr, val)

        if config.environment is None:
            config.environment = app.config.get("ENV", "development")

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
