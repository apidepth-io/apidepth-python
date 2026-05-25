"""Django integration for the Apidepth SDK.

Activate by adding the app to ``INSTALLED_APPS`` and providing settings::

    # settings.py
    INSTALLED_APPS = [
        ...
        "apidepth.integrations.django",
    ]

    APIDEPTH = {
        "api_key": env("APIDEPTH_API_KEY"),
        "environment": env("DJANGO_ENV", default="development"),
        # "sample_rate":    0.5,
        # "ignored_hosts":  ["internal.example.com"],
        # "extra_vendors":  {"my-payments": "api.payments.internal"},
        # "flush_interval": 20,
    }

The ``ApidepthConfig.ready()`` method runs after all other ``AppConfig``
``ready()`` calls are complete, which means:

* All third-party packages (including those that reopen HTTP clients) have
  been imported and settled.
* The user's ``APIDEPTH`` settings dict has been applied to the
  configuration singleton.
* HTTP client monkey-patching is safe to perform.

This mirrors the sequencing guarantees of the Ruby gem's Railtie.
"""

from __future__ import annotations

import logging

from django.apps import AppConfig


class ApidepthConfig(AppConfig):
    """Django ``AppConfig`` that bootstraps the Apidepth SDK at startup.

    Responsibilities (in order):

    1. Apply the ``APIDEPTH`` settings dict from ``django.conf.settings``
       onto the configuration singleton.
    2. Route the SDK logger through Django's logging system.
    3. Warn if no API key is present.
    4. Monkey-patch HTTP clients via :func:`~apidepth.instrumentation.instrument`.
    5. Merge ``extra_vendors`` into the vendor registry.
    6. Kick off the remote registry fetch and the background refresh thread
       via :func:`~apidepth.registry_loader.load_and_start`.
    """

    name = "apidepth.integrations.django"
    label = "apidepth"
    verbose_name = "Apidepth"

    def ready(self) -> None:
        """Bootstrap the SDK.  Called by Django after all apps are loaded.

        All work is deferred to this method (rather than ``__init__``) so
        the Django application registry is fully populated before the SDK
        touches any HTTP client or starts background threads.
        """
        import apidepth
        from apidepth import instrumentation, registry_loader
        from apidepth.vendor_registry import VendorRegistry

        config = apidepth.get_configuration()

        # --- 1. Apply APIDEPTH settings dict ------------------------------------
        # Wrapped in try/except so a missing or broken settings module
        # degrades gracefully rather than preventing the app from starting.
        try:
            from django.conf import settings as django_settings

            apidepth_settings = getattr(django_settings, "APIDEPTH", {})
            apidepth.configure(**apidepth_settings)
        except Exception as exc:
            logging.getLogger("apidepth").warning(
                "[Apidepth] Failed to apply APIDEPTH settings: %s. "
                "Check your settings.py for invalid keys or values.",
                exc,
            )

        # --- 2. Route SDK logs through Django's logging system -----------------
        apidepth.set_logger(logging.getLogger("apidepth"))

        # --- 3. Warn on missing API key ----------------------------------------
        if config.api_key is None:
            logging.getLogger("apidepth").warning(
                "[Apidepth] No API key configured — events will not be delivered. "
                "Visit www.apidepth.io to create an account and get your key, "
                "then add APIDEPTH = {'api_key': ...} to your settings."
            )

        # --- 4–6. Instrument, load extra vendors, start registry ---------------
        instrumentation.instrument()
        VendorRegistry.load_extra_vendors(config.extra_vendors)
        registry_loader.load_and_start()

        logging.getLogger("apidepth").debug(
            "[Apidepth] Instrumentation active — registry=%s vendors=%d",
            VendorRegistry.version(),
            VendorRegistry.vendor_count(),
        )
