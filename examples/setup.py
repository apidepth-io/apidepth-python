"""
Standalone script / non-framework usage example.

For Django: add 'apidepth.integrations.django' to INSTALLED_APPS and set
APIDEPTH = {'api_key': ...} in settings.py.

For Flask: see apidepth/integrations/flask.py.
"""
import os

import apidepth

apidepth.configure(
    api_key=os.environ["APIDEPTH_API_KEY"],

    # Tag every event with the deployment environment.
    environment="production",

    # Capture half of all requests (useful for very high-throughput apps).
    # sample_rate=0.5,

    # Never record calls to internal services.
    # ignored_hosts=["internal.mycompany.com"],

    # Map an in-house API to a friendly vendor name in the dashboard.
    # extra_vendors={"payments-api": "api.payments.internal"},

    # Route errors to your own error tracker.
    # on_flush_error=lambda exc, ctx: sentry_sdk.capture_exception(exc),
)

# Patch requests (and httpx if installed) before any outbound calls are made.
apidepth.instrument()


# ---------------------------------------------------------------------------
# After instrument(), all requests / httpx calls are tracked automatically.
# ---------------------------------------------------------------------------

import requests  # noqa: E402

resp = requests.get("https://api.stripe.com/v1/charges/ch_abc123",
                    headers={"Authorization": "Bearer sk_test_..."})
print(resp.status_code)

# Inspect queue / flush stats for debugging
from apidepth.collector import Collector
print(Collector.instance().stats())
