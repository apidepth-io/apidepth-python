"""Tests for HTTP client instrumentation (requests and httpx).

Ordering note
-------------
``responses`` patches ``requests.adapters.HTTPAdapter.send`` via
``unittest.mock.patch``.  Our instrumentation also patches the same method.
When ``@responses_mock.activate`` wraps a test, responses' patch is applied
first (the decorator fires before the test body), so our ``_patched_send``
must be installed **after** responses activates to sit on top of the call
chain.  Tests that need both call ``instrumentation.instrument()`` inside
the test body (after the ``@responses_mock.activate`` decorator has already
activated its mock).  Tests that do not need responses activate instrumentation
in the normal way.
"""
import httpx
import pytest
import requests
import requests.adapters
import respx
import responses as responses_mock

import apidepth
from apidepth.collector import Collector
from apidepth import instrumentation


# Save the true originals once at import time, before any patching happens.
_ORIGINAL_REQUESTS_SEND = requests.adapters.HTTPAdapter.send
_ORIGINAL_HTTPX_SYNC_SEND = httpx.Client.send
_ORIGINAL_HTTPX_ASYNC_SEND = httpx.AsyncClient.send


def _restore_originals():
    requests.adapters.HTTPAdapter.send = _ORIGINAL_REQUESTS_SEND
    httpx.Client.send = _ORIGINAL_HTTPX_SYNC_SEND
    httpx.AsyncClient.send = _ORIGINAL_HTTPX_ASYNC_SEND
    instrumentation._requests_patched = False
    instrumentation._httpx_patched = False


@pytest.fixture(autouse=True)
def reset_state():
    """Reset config, collector, and instrumentation between every test."""
    apidepth._configuration = None
    Collector.reset()
    _restore_originals()
    yield
    _restore_originals()
    Collector.reset()
    apidepth._configuration = None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_instrument_is_idempotent():
    apidepth.configure(api_key="apd_live_test", environment="test")
    instrumentation.instrument()
    method_after_first = requests.adapters.HTTPAdapter.send
    instrumentation.instrument()
    instrumentation.instrument()
    assert requests.adapters.HTTPAdapter.send is method_after_first


def test_requests_patched_flag_set_only_once():
    apidepth.configure(api_key="apd_live_test")
    instrumentation.instrument()
    assert instrumentation._requests_patched is True
    instrumentation.instrument()
    assert instrumentation._requests_patched is True


# ---------------------------------------------------------------------------
# requests: successful recording
# Instrument INSIDE the responses context so our patch sits on top.
# ---------------------------------------------------------------------------

@responses_mock.activate
def test_requests_get_to_stripe_records_one_event():
    # Instrument after @responses_mock.activate has patched HTTPAdapter.send
    apidepth.configure(api_key="apd_live_test", environment="test")
    instrumentation.instrument()

    responses_mock.add(
        responses_mock.GET,
        "https://api.stripe.com/v1/charges",
        json={"data": []},
        status=200,
    )
    requests.get("https://api.stripe.com/v1/charges")
    col = Collector.instance()
    assert col.stats()["queue_size"] == 1


@responses_mock.activate
def test_requests_get_to_unknown_host_records_nothing():
    apidepth.configure(api_key="apd_live_test")
    instrumentation.instrument()

    responses_mock.add(
        responses_mock.GET,
        "https://unknown.example.com/api/stuff",
        json={},
        status=200,
    )
    requests.get("https://unknown.example.com/api/stuff")
    col = Collector.instance()
    assert col.stats()["queue_size"] == 0


# ---------------------------------------------------------------------------
# requests: config gates
# ---------------------------------------------------------------------------

@responses_mock.activate
def test_requests_disabled_config_records_nothing():
    apidepth.configure(api_key="key", enabled=False)
    instrumentation.instrument()
    responses_mock.add(
        responses_mock.GET,
        "https://api.stripe.com/v1/charges",
        json={},
        status=200,
    )
    requests.get("https://api.stripe.com/v1/charges")
    assert Collector.instance().stats()["queue_size"] == 0


@responses_mock.activate
def test_requests_sample_rate_zero_records_nothing():
    apidepth.configure(api_key="key", sample_rate=0.0)
    instrumentation.instrument()
    responses_mock.add(
        responses_mock.GET,
        "https://api.stripe.com/v1/charges",
        json={},
        status=200,
    )
    requests.get("https://api.stripe.com/v1/charges")
    assert Collector.instance().stats()["queue_size"] == 0


@responses_mock.activate
def test_requests_ignored_host_records_nothing():
    apidepth.configure(api_key="key", ignored_hosts=["api.stripe.com"])
    instrumentation.instrument()
    responses_mock.add(
        responses_mock.GET,
        "https://api.stripe.com/v1/charges",
        json={},
        status=200,
    )
    requests.get("https://api.stripe.com/v1/charges")
    assert Collector.instance().stats()["queue_size"] == 0


# ---------------------------------------------------------------------------
# requests: timeout recording
# ---------------------------------------------------------------------------

def test_requests_timeout_records_timeout_event():
    import requests.exceptions

    apidepth.configure(api_key="apd_live_test", environment="test")

    with responses_mock.RequestsMock() as rsps:
        # Instrument inside the context so our patch wraps rsps' mock
        instrumentation.instrument()
        rsps.add(
            responses_mock.GET,
            "https://api.stripe.com/v1/charges",
            body=requests.exceptions.Timeout("timed out"),
        )
        with pytest.raises(requests.exceptions.Timeout):
            requests.get("https://api.stripe.com/v1/charges")

    col = Collector.instance()
    assert col.stats()["queue_size"] == 1
    events = col._drain_queue()
    assert len(events) == 1
    assert events[0]["outcome"] == "timeout"


# ---------------------------------------------------------------------------
# httpx: successful recording
# respx works differently — it patches at the transport level, not Client.send,
# so our instrumentation (patching Client.send) sits above it correctly.
# ---------------------------------------------------------------------------

@respx.mock
def test_httpx_get_to_stripe_records_one_event():
    apidepth.configure(api_key="apd_live_test", environment="test")
    instrumentation.instrument()
    respx.get("https://api.stripe.com/v1/charges").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    httpx.get("https://api.stripe.com/v1/charges")
    assert Collector.instance().stats()["queue_size"] == 1


# ---------------------------------------------------------------------------
# httpx: timeout recording
# ---------------------------------------------------------------------------

@respx.mock
def test_httpx_timeout_records_timeout_event():
    apidepth.configure(api_key="apd_live_test", environment="test")
    instrumentation.instrument()
    respx.get("https://api.stripe.com/v1/charges").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    with pytest.raises(httpx.TimeoutException):
        httpx.get("https://api.stripe.com/v1/charges")

    col = Collector.instance()
    assert col.stats()["queue_size"] == 1
    events = col._drain_queue()
    assert events[0]["outcome"] == "timeout"


# ---------------------------------------------------------------------------
# Recorded event field validation
# ---------------------------------------------------------------------------

@responses_mock.activate
def test_recorded_event_has_correct_fields():
    apidepth.configure(api_key="apd_live_test", environment="test")
    instrumentation.instrument()
    responses_mock.add(
        responses_mock.GET,
        "https://api.stripe.com/v1/charges",
        json={"data": []},
        status=200,
    )
    requests.get("https://api.stripe.com/v1/charges")

    col = Collector.instance()
    events = col._drain_queue()
    assert len(events) == 1
    evt = events[0]

    assert evt["vendor"] == "stripe"
    assert evt["method"] == "GET"
    assert evt["status"] == 200
    assert evt["outcome"] == "success"
    assert evt["cold_start"] is False
    assert evt["env"] == "test"
    assert isinstance(evt["duration_ms"], int)
    assert isinstance(evt["ts"], int)
    assert evt["ts"] > 0
    assert "endpoint" in evt


@responses_mock.activate
def test_recorded_event_endpoint_is_normalised():
    apidepth.configure(api_key="apd_live_test", environment="test")
    instrumentation.instrument()
    responses_mock.add(
        responses_mock.GET,
        "https://api.stripe.com/v1/charges/ch_abc123",
        json={},
        status=200,
    )
    requests.get("https://api.stripe.com/v1/charges/ch_abc123")
    events = Collector.instance()._drain_queue()
    assert events[0]["endpoint"] == "/v1/charges/:id"


@responses_mock.activate
def test_recorded_event_server_error_outcome():
    apidepth.configure(api_key="apd_live_test", environment="test")
    instrumentation.instrument()
    responses_mock.add(
        responses_mock.GET,
        "https://api.stripe.com/v1/charges",
        json={"error": "internal"},
        status=500,
    )
    requests.get("https://api.stripe.com/v1/charges")
    events = Collector.instance()._drain_queue()
    assert events[0]["outcome"] == "server_error"


@responses_mock.activate
def test_recorded_event_client_error_outcome():
    apidepth.configure(api_key="apd_live_test", environment="test")
    instrumentation.instrument()
    responses_mock.add(
        responses_mock.GET,
        "https://api.stripe.com/v1/charges",
        json={"error": "not found"},
        status=404,
    )
    requests.get("https://api.stripe.com/v1/charges")
    events = Collector.instance()._drain_queue()
    assert events[0]["outcome"] == "client_error"
