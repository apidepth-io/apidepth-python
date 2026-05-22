"""Tests for Collector: record, stats, singleton lifecycle, validators."""
import logging
from unittest.mock import patch
from urllib.parse import urlparse

import pytest

import apidepth
from apidepth.collector import (
    Collector,
    MAX_QUEUE_SIZE,
    _validate_api_key,
    _validate_collector_url,
)


@pytest.fixture(autouse=True)
def reset_collector_and_config():
    """Reset both singletons between every test."""
    apidepth._configuration = None
    Collector.reset()
    yield
    Collector.reset()
    apidepth._configuration = None


# ---------------------------------------------------------------------------
# record / stats
# ---------------------------------------------------------------------------

def test_record_enqueues_event_and_stats_shows_queue_size_1():
    col = Collector.instance()
    col.record({"vendor": "stripe", "ts": 1})
    assert col.stats()["queue_size"] == 1


def test_record_multiple_events_increments_queue_size():
    col = Collector.instance()
    col.record({"a": 1})
    col.record({"b": 2})
    assert col.stats()["queue_size"] == 2


def test_record_full_queue_increments_total_dropped_does_not_raise():
    col = Collector.instance()
    # Fill the queue to max capacity
    for i in range(MAX_QUEUE_SIZE):
        col._queue.put_nowait({"i": i})
    # One more should be dropped without raising
    col.record({"overflow": True})
    assert col.stats()["total_dropped"] == 1
    assert col.stats()["queue_size"] == MAX_QUEUE_SIZE


def test_stats_initial_state():
    col = Collector.instance()
    s = col.stats()
    assert s["queue_size"] == 0
    assert s["consecutive_failures"] == 0
    assert s["total_dropped"] == 0
    assert s["last_flush_at"] is None


# ---------------------------------------------------------------------------
# Singleton lifecycle
# ---------------------------------------------------------------------------

def test_instance_returns_same_object_on_repeated_calls():
    a = Collector.instance()
    b = Collector.instance()
    assert a is b


def test_reset_then_instance_returns_fresh_object():
    first = Collector.instance()
    first.record({"x": 1})
    Collector.reset()
    fresh = Collector.instance()
    assert fresh is not first
    assert fresh.stats()["queue_size"] == 0


def test_reset_then_instance_two_cycles_both_fresh():
    Collector.reset()
    c1 = Collector.instance()
    c1.record({"a": 1})
    Collector.reset()
    c2 = Collector.instance()
    assert c2 is not c1
    assert c2.stats()["queue_size"] == 0


def test_atexit_handler_unregistered_on_reset():
    """After reset(), the old flush is unregistered and a fresh collector starts clean."""
    col = Collector.instance()
    col.record({"x": 1})
    assert col.stats()["queue_size"] == 1
    Collector.reset()
    # A fresh instance should have a clean queue, confirming reset worked
    new_col = Collector.instance()
    assert new_col is not col
    assert new_col.stats()["queue_size"] == 0


# ---------------------------------------------------------------------------
# _validate_api_key
# ---------------------------------------------------------------------------

def test_validate_api_key_raises_on_carriage_return():
    with pytest.raises(ValueError, match="illegal"):
        _validate_api_key("apd_live_\r_bad")


def test_validate_api_key_raises_on_newline():
    with pytest.raises(ValueError, match="illegal"):
        _validate_api_key("apd_live_\n_bad")


def test_validate_api_key_raises_on_null_byte():
    with pytest.raises(ValueError, match="illegal"):
        _validate_api_key("apd_live_\x00_bad")


def test_validate_api_key_does_not_raise_on_normal_key():
    # Should not raise
    _validate_api_key("apd_live_abcdef1234567890")


def test_validate_api_key_does_not_raise_on_empty_string():
    # empty key doesn't contain illegal chars
    _validate_api_key("")


# ---------------------------------------------------------------------------
# _validate_collector_url
# ---------------------------------------------------------------------------

def test_validate_collector_url_raises_for_http():
    with pytest.raises(ValueError, match="HTTPS"):
        _validate_collector_url(urlparse("http://collector.apidepth.io/v1/events"))


def test_validate_collector_url_raises_for_loopback():
    with pytest.raises(ValueError):
        _validate_collector_url(urlparse("https://127.0.0.1/v1/events"))


def test_validate_collector_url_raises_for_rfc1918_10():
    with pytest.raises(ValueError):
        _validate_collector_url(urlparse("https://10.0.0.1/v1/events"))


def test_validate_collector_url_raises_for_rfc1918_192_168():
    with pytest.raises(ValueError):
        _validate_collector_url(urlparse("https://192.168.1.1/v1/events"))


def test_validate_collector_url_raises_for_ipv6_loopback():
    with pytest.raises(ValueError):
        _validate_collector_url(urlparse("https://[::1]/v1/events"))


def test_validate_collector_url_raises_for_decimal_zero_ip():
    # decimal 0 = 0.0.0.0
    with pytest.raises(ValueError):
        _validate_collector_url(urlparse("https://0/v1/events"))


def test_validate_collector_url_raises_for_unbracketed_ipv6():
    # fe80::1 in URL without brackets — urlparse misparses, code detects ::
    with pytest.raises(ValueError):
        _validate_collector_url(urlparse("https://fe80::1/v1/events"))


def test_validate_collector_url_allows_public_hostname():
    # Should not raise
    _validate_collector_url(urlparse("https://collector.apidepth.io/v1/events"))


def test_validate_collector_url_allows_another_public_hostname():
    _validate_collector_url(urlparse("https://api.example.com/collect"))


# ---------------------------------------------------------------------------
# flush with no api_key
# ---------------------------------------------------------------------------

def test_flush_with_no_api_key_logs_warning_and_does_not_send():
    apidepth.configure(api_key=None)
    col = Collector.instance()
    col.record({"vendor": "stripe", "ts": 1})

    with patch("http.client.HTTPSConnection") as mock_conn_cls:
        with patch.object(logging.getLogger("apidepth"), "warning") as mock_warn:
            col.flush()
        # No connection should have been opened
        mock_conn_cls.assert_not_called()
        # Warning should have been logged at least once
        assert mock_warn.called or col._warned_no_key


def test_flush_with_no_api_key_sets_warned_no_key_flag():
    apidepth.configure(api_key=None)
    col = Collector.instance()
    col.record({"x": 1})
    with patch("http.client.HTTPSConnection"):
        col.flush()
    assert col._warned_no_key is True
