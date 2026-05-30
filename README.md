# apidepth-python

[![PyPI](https://img.shields.io/pypi/v/apidepth)](https://pypi.org/project/apidepth/)
[![Python](https://img.shields.io/pypi/pyversions/apidepth)](https://pypi.org/project/apidepth/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Track outbound API latency, error rates, and rate limit quota across your third-party vendors — Stripe, OpenAI, Anthropic, Twilio, GitHub, and more.

Zero config for supported vendors. No code changes to your existing HTTP calls.

---

## How it works

**Real traffic, not synthetic probes.** Every outbound HTTP call your application makes to a known vendor is timed at the socket level, tagged with outcome and environment metadata, and batched to the Apidepth collector in the background. The latency number in your dashboard is the number your users feel — not a probe running from a data center somewhere else.

**Fleet benchmarking.** Because Apidepth aggregates anonymized timing data across all customers, your dashboard shows not just "your Stripe p95 is 420ms" but "the fleet median is 280ms — you may have a regional routing issue." That comparison is only possible with real traffic from real deployments, which is why no synthetic probe tool can offer it.

**Proof of Innocence.** When all endpoints to a vendor spike simultaneously, Apidepth surfaces a verdict: *isolated* (the spike is yours alone — likely your code or infrastructure) or *tracking* (the fleet sees the same thing — vendor-side). The attribution card makes it fast to tell ops "it's Stripe, not us."

**Alerts and weekly digest.** Apidepth fires alerts when vendor latency crosses your configured threshold and sends a weekly digest summarizing what changed. Monitoring without alerting is passive; this is working for you.

**Rate limit intelligence.** Apidepth tracks 429 patterns and projects quota burn-down before you hit the ceiling — with a burn-down card showing time-to-throttle at current request rate.

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

## Getting started

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
from apidepth import registry_loader

apidepth.configure(
    api_key=os.environ["APIDEPTH_API_KEY"],
    environment="production",
)
apidepth.instrument()       # call before any outbound HTTP
registry_loader.load_and_start()  # loads remote vendor registry + starts refresh thread

import requests
resp = requests.get("https://api.stripe.com/v1/charges/ch_abc123", ...)
```

`load_and_start()` fetches the latest vendor registry from the network (with a local disk cache fallback) and starts a background refresh thread. Without it, only the six bundled vendors are recognised. Django and Flask integrations call this automatically.

For **Gunicorn / uWSGI**, call `Collector.register_fork_safety()` once before the server forks so each worker gets its own flush thread:

```python
from apidepth.collector import Collector
Collector.register_fork_safety()
```

---

## CLI

The SDK ships two subcommands for setup and connectivity verification.

### `python -m apidepth setup`

Interactive wizard that detects your framework (Django, FastAPI, or generic), generates the correct initializer snippet, and optionally writes it to disk.

```bash
python -m apidepth setup
```

For CI/CD pipelines, skip all prompts:

```bash
python -m apidepth setup --api-key $APIDEPTH_API_KEY --no-prompt
```

| Flag | Description |
|---|---|
| `--api-key <key>` | Inject your API key into the generated snippet. |
| `--no-prompt` | Non-interactive mode — print snippet to stdout and exit. |
| `--framework <name>` | Override auto-detection (`django`, `fastapi`, `generic`). |
| `--ignored-hosts <patterns>` | Comma-separated host patterns to add to `ignored_hosts` (glob wildcards supported). |
| `--collector-url <url>` | Override the collector URL in the generated snippet. |

### `python -m apidepth test`

Sends a synthetic test event to the collector and confirms the pipeline is working end-to-end. Reads `APIDEPTH_API_KEY` (and optionally `APIDEPTH_COLLECTOR_URL`) from the environment. Prints the round-trip time on success, or a per-failure-mode error message with next steps on failure.

```bash
python -m apidepth test
# ✓ received in 142ms
# Visit your dashboard: https://apidepth.io/dashboard
```

Exits with code 1 on any error (bad key, unreachable, SSL failure, timeout).

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

| Field | Description |
|---|---|
| `vendor` | Vendor slug, e.g. `"stripe"`, `"openai"` |
| `endpoint` | Normalized path, e.g. `"/v1/charges/:id"` |
| `method` | HTTP verb: `"GET"`, `"POST"`, etc. |
| `status` | HTTP status code, or `None` on timeout |
| `outcome` | `"success"`, `"client_error"`, `"server_error"`, `"timeout"`, `"unknown"` |
| `duration_ms` | Wall-clock time in milliseconds |
| `cold_start` | `True` on the first request to a host in this process; `False` thereafter |
| `env` | Environment tag from `environment` config option |
| `ts` | Unix timestamp in milliseconds |
| `rl_remaining` | Remaining quota, e.g. `4999` — present when vendor rate limit headers are found |
| `rl_limit` | Total quota, e.g. `5000` — present when vendor rate limit headers are found |
| `rl_reset_at` | Quota reset time in epoch milliseconds — present when vendor rate limit headers are found |

### What is never captured

- Request or response **bodies**
- Request or response **headers** (including Authorization)
- **Query string parameters**
- Any credential, token, or secret your application uses to authenticate with a vendor
- User identifiers or PII of any kind

Path normalization strips resource IDs before the event leaves your server. `/v1/charges/ch_3Ox4Kz2e` becomes `/v1/charges/:id`.

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

---

## Differences from the Ruby gem

### `cold_start` detection

The Ruby gem uses `Net::HTTP#started?` to tag the first request on each TCP connection. The Python SDK cannot inspect socket reuse via a public API, so it uses a per-process host registry instead: the **first request to each hostname** within a process lifetime is tagged `cold_start: true`. This accurately captures DNS + TCP + TLS overhead for the cases that matter most — process startup and serverless invocations.

Forked workers (gunicorn, uWSGI) each get a fresh registry via `os.register_at_fork`, so the first request per worker is correctly tagged cold.

**Known limitation:** mid-process connection re-establishment after a keep-alive timeout is not detected. For long-lived, low-throughput services this means occasional reconnect latency won't be excluded from percentile calculations. For high-throughput services and serverless workers the behaviour is equivalent to the Ruby gem.
