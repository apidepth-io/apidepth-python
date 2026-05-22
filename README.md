# apidepth-python

Track outbound API latency, error rates, and rate limit quota across your third-party vendors — Stripe, OpenAI, Anthropic, Twilio, GitHub, and more.

Zero config for supported vendors. No code changes to your existing HTTP calls.

---

## Installation

```bash
pip install apidepth
```

For `requests` instrumentation (most common):
```bash
pip install "apidepth[requests]"
```

For `httpx` instrumentation:
```bash
pip install "apidepth[httpx]"
```

---

## Quick start

### Django

Add to `INSTALLED_APPS` and configure in `settings.py`:

```python
INSTALLED_APPS = [
    ...
    "apidepth.integrations.django",
]

APIDEPTH = {
    "api_key": env("APIDEPTH_API_KEY"),
    "environment": env("DJANGO_ENV", default="development"),
}
```

### Flask

```python
from flask import Flask
from apidepth.integrations.flask import Apidepth

app = Flask(__name__)
app.config["APIDEPTH_API_KEY"] = os.environ["APIDEPTH_API_KEY"]
app.config["APIDEPTH_ENVIRONMENT"] = "production"

Apidepth(app)
```

### Standalone / scripts

```python
import apidepth

apidepth.configure(
    api_key=os.environ["APIDEPTH_API_KEY"],
    environment="production",
)
apidepth.instrument()   # call before any outbound HTTP

import requests
resp = requests.get("https://api.stripe.com/v1/charges/ch_abc123", ...)
```

---

## Configuration

| Option | Default | Description |
|---|---|---|
| `api_key` | `None` | **Required.** Your Apidepth API key. |
| `environment` | `None` | Deployment environment tag, e.g. `"production"`. |
| `enabled` | `True` | Set `False` to disable all instrumentation. |
| `sample_rate` | `1.0` | Float 0.0–1.0. Fraction of requests to capture. |
| `ignored_hosts` | `[]` | List of hostnames to never record. |
| `extra_vendors` | `{}` | Map `{"vendor-name": "host"}` for in-house APIs. |
| `flush_interval` | `20` | Background flush interval in seconds. |
| `registry_cache_path` | `/tmp/apidepth_registry.json` | Disk cache for the vendor registry. |
| `registry_refresh_interval` | `21600` | Registry refresh interval in seconds (6 h). |
| `on_flush_error` | `None` | `Callable(exc, ctx)` for routing errors to Sentry etc. |
| `collector_url` | production endpoint | Override for self-hosted collectors. |

---

## What gets captured

Every outbound HTTP request to a recognised vendor produces one event:

| Field | Example |
|---|---|
| `vendor` | `"stripe"` |
| `endpoint` | `"/v1/charges/:id"` |
| `method` | `"POST"` |
| `status` | `200` |
| `outcome` | `"success"` / `"client_error"` / `"server_error"` / `"timeout"` |
| `duration_ms` | `234` |
| `cold_start` | `false` |
| `env` | `"production"` |
| `ts` | `1747008000000` (epoch ms) |
| `rl_remaining` | `4999` (when rate limit headers present) |
| `rl_limit` | `5000` |
| `rl_reset_at` | `1747008060000` (epoch ms) |

**Never captured:** request/response bodies, headers, query parameters, credentials.

---

## Supported vendors

| Vendor | Host |
|---|---|
| Stripe | `api.stripe.com` |
| OpenAI | `api.openai.com` |
| Anthropic | `api.anthropic.com` |
| Twilio | `api.twilio.com` |
| Resend | `api.resend.com` |
| GitHub | `api.github.com` |

Additional vendors are loaded from the remote registry every 6 hours.

### Custom vendors

```python
apidepth.configure(
    extra_vendors={"payments-api": "api.payments.internal"},
)
```

---

## Rate limit tracking

The SDK extracts quota state from response headers and includes it in every event.
Supported header families (checked in priority order):

- **OpenAI / Anthropic**: `x-ratelimit-remaining-requests`, `x-ratelimit-limit-requests`, `x-ratelimit-reset-requests`
- **GitHub**: `x-ratelimit-remaining`, `x-ratelimit-limit`, `x-ratelimit-reset`
- **IETF draft / HubSpot**: `ratelimit-remaining`, `ratelimit-limit`, `ratelimit-reset`
- **Stripe / generic 429**: `retry-after`

Reset values are normalised to epoch milliseconds regardless of the source format (Unix timestamp, seconds-from-now, OpenAI duration strings like `"1m30s"`).

---

## Debugging

```python
from apidepth.collector import Collector

print(Collector.instance().stats())
# {
#   'queue_size': 0,
#   'consecutive_failures': 0,
#   'total_dropped': 0,
#   'last_flush_at': 1747008000.123,
# }
```

---

## Framework compatibility

| Framework | Version | Integration |
|---|---|---|
| Django | 3.2+ | `apidepth.integrations.django` in `INSTALLED_APPS` |
| Flask | 2.0+ | `Apidepth(app)` |
| FastAPI / Starlette | any | Call `apidepth.instrument()` at startup |
| Scripts / workers | — | Call `apidepth.instrument()` at startup |

---

## Python compatibility

Python 3.9–3.13. No required runtime dependencies (stdlib only). `requests` and `httpx` are optional instrumentation targets detected at runtime.
