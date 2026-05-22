"""Django integration for Apidepth.

Add 'apidepth.integrations.django' to INSTALLED_APPS to activate.
The AppConfig reads APIDEPTH settings from Django's settings module,
instruments HTTP clients, loads the remote vendor registry, and registers
an at-exit flush — mirroring what the Ruby gem's Railtie does.

Example settings.py::

    INSTALLED_APPS = [
        ...
        "apidepth.integrations.django",
    ]

    APIDEPTH = {
        "api_key": env("APIDEPTH_API_KEY"),
        "environment": env("DJANGO_ENV", default="development"),
        # "sample_rate": 0.5,
        # "ignored_hosts": ["internal.example.com"],
        # "extra_vendors": {"my-payments": "api.payments.internal"},
    }
"""
from __future__ import annotations

import logging

from django.apps import AppConfig


class ApidepthConfig(AppConfig):
    name = "apidepth.integrations.django"
    label = "apidepth"
    verbose_name = "Apidepth"

    def ready(self) -> None:
        import apidepth
        from apidepth import instrumentation, registry_loader
        from apidepth.vendor_registry import VendorRegistry

        config = apidepth.get_configuration()

        # Apply settings from Django's settings module.
        try:
            from django.conf import settings as django_settings
            apidepth_settings = getattr(django_settings, "APIDEPTH", {})
            for key, value in apidepth_settings.items():
                setattr(config, key, value)
        except Exception:
            pass

        # Bind apidepth's logger to Django's logging system.
        apidepth.set_logger(logging.getLogger("apidepth"))

        if config.api_key is None:
            logging.getLogger("apidepth").warning(
                "[Apidepth] No API key configured — events will not be delivered. "
                "Visit www.apidepth.io to create an account and get your key, "
                "then add APIDEPTH = {'api_key': ...} to your settings."
            )

        # Instrument HTTP clients and load the vendor registry.
        instrumentation.instrument()
        VendorRegistry.load_extra_vendors(config.extra_vendors)
        registry_loader.load_and_start()

        logging.getLogger("apidepth").debug(
            "[Apidepth] Instrumentation active — registry=%s vendors=%d",
            VendorRegistry.version(),
            VendorRegistry.vendor_count(),
        )


# Allow `"apidepth.integrations.django"` as the INSTALLED_APPS entry.
default_app_config = "apidepth.integrations.django.ApidepthConfig"
