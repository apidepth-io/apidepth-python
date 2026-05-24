"""Tests for apidepth.registry_loader — fetch, cache, disk I/O, and warning logic."""

import json
import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

import apidepth
import apidepth.registry_loader as rl
from apidepth.configuration import Configuration
from apidepth.vendor_registry import BUNDLED_BASELINE, VendorRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_warn_state():
    """Clear module-level warn-once dicts before and after each test."""

    def _clear():
        with rl._lock:
            rl._conflict_vendors.clear()
            rl._warned_stale.clear()
            rl._warned_conflict.clear()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def restore_registry():
    """Restore VendorRegistry to bundled-baseline state between tests."""
    from apidepth.vendor_registry import _build_hosts, _build_patterns

    hosts = _build_hosts(BUNDLED_BASELINE)
    patterns = _build_patterns(BUNDLED_BASELINE)
    version = BUNDLED_BASELINE["version"]
    yield
    with VendorRegistry._lock:
        VendorRegistry._hosts = hosts
        VendorRegistry._patterns = patterns
        VendorRegistry._version = version


@pytest.fixture(autouse=True)
def reset_configuration():
    """Prevent configuration state from leaking between tests."""
    apidepth._configuration = None
    yield
    apidepth._configuration = None


@pytest.fixture
def cfg():
    """Configuration with a live API key and a temp-dir cache path."""
    c = Configuration()
    c.api_key = "apd_live_testkey123456"
    with tempfile.TemporaryDirectory() as tmpdir:
        c.registry_cache_path = os.path.join(tmpdir, "registry.json")
        yield c


@pytest.fixture
def minimal_registry():
    return {"version": "test-1", "vendors": []}


def _mock_conn(status: int, body: bytes) -> MagicMock:
    """Return a mock HTTPSConnection that responds with (status, body)."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    conn = MagicMock()
    conn.getresponse.return_value = resp
    return conn


# ---------------------------------------------------------------------------
# _sanitize
# ---------------------------------------------------------------------------


def test_sanitize_strips_cr_lf_tab():
    result = rl._sanitize("a\rb\nc\td")
    assert "\r" not in result
    assert "\n" not in result
    assert "\t" not in result


def test_sanitize_truncates_at_200_chars():
    assert len(rl._sanitize("x" * 300)) == 200


def test_sanitize_preserves_short_string():
    assert rl._sanitize("hello") == "hello"


def test_sanitize_coerces_non_string_to_str():
    assert rl._sanitize(99) == "99"


# ---------------------------------------------------------------------------
# _validate_cache_path
# ---------------------------------------------------------------------------


def test_validate_accepts_absolute_path():
    rl._validate_cache_path("/tmp/registry.json")  # no exception  # nosec B108


def test_validate_accepts_deeply_nested_absolute_path():
    rl._validate_cache_path("/var/lib/app/cache/apidepth_registry.json")


def test_validate_rejects_relative_path():
    with pytest.raises(ValueError):
        rl._validate_cache_path("relative/path.json")


def test_validate_rejects_dot_dot_traversal():
    with pytest.raises(ValueError):
        rl._validate_cache_path("/tmp/../etc/passwd")  # nosec B108


def test_validate_rejects_none():
    with pytest.raises(ValueError):
        rl._validate_cache_path(None)


def test_validate_rejects_integer():
    with pytest.raises(ValueError):
        rl._validate_cache_path(42)


# ---------------------------------------------------------------------------
# _load_from_disk
# ---------------------------------------------------------------------------


def test_load_from_disk_returns_none_when_file_missing(cfg):
    assert rl._load_from_disk(cfg) is None


def test_load_from_disk_returns_parsed_registry(cfg, minimal_registry):
    with open(cfg.registry_cache_path, "wb") as f:
        f.write(json.dumps(minimal_registry).encode())
    assert rl._load_from_disk(cfg) == minimal_registry


def test_load_from_disk_returns_none_on_invalid_json(cfg):
    with open(cfg.registry_cache_path, "wb") as f:
        f.write(b"not valid json {{")
    assert rl._load_from_disk(cfg) is None


def test_load_from_disk_returns_none_on_invalid_cache_path():
    c = Configuration()
    c.registry_cache_path = "relative.json"
    assert rl._load_from_disk(c) is None


def test_load_from_disk_returns_none_on_read_error(cfg):
    with open(cfg.registry_cache_path, "wb") as f:
        f.write(b"{}")
    with patch("builtins.open", side_effect=PermissionError("denied")):
        assert rl._load_from_disk(cfg) is None


# ---------------------------------------------------------------------------
# _write_cache
# ---------------------------------------------------------------------------


def test_write_cache_persists_bytes(cfg):
    data = b'{"version": "cached-1"}'
    rl._write_cache(cfg, data)
    with open(cfg.registry_cache_path, "rb") as f:
        assert f.read() == data


def test_write_cache_silent_on_relative_path():
    c = Configuration()
    c.registry_cache_path = "relative.json"
    rl._write_cache(c, b"{}")  # must not raise


def test_write_cache_silent_on_write_error(cfg):
    with patch("builtins.open", side_effect=PermissionError("denied")):
        rl._write_cache(cfg, b"{}")  # must not raise


# ---------------------------------------------------------------------------
# _emit_stale_warnings
# ---------------------------------------------------------------------------


def test_emit_stale_logs_warning_for_vendor(caplog):
    with caplog.at_level("WARNING", logger="apidepth"):
        rl._emit_stale_warnings(["slowpay"])
    assert "slowpay" in caplog.text
    assert "7+ days" in caplog.text


def test_emit_stale_warns_only_once_per_vendor(caplog):
    with caplog.at_level("WARNING", logger="apidepth"):
        rl._emit_stale_warnings(["stripe"])
        rl._emit_stale_warnings(["stripe"])
    assert caplog.text.count("stripe") == 1


def test_emit_stale_warns_all_new_vendors(caplog):
    with caplog.at_level("WARNING", logger="apidepth"):
        rl._emit_stale_warnings(["vendor-a", "vendor-b"])
    assert "vendor-a" in caplog.text
    assert "vendor-b" in caplog.text


def test_emit_stale_ignores_non_list():
    rl._emit_stale_warnings("not-a-list")  # must not raise


def test_emit_stale_skips_non_string_entries(caplog):
    with caplog.at_level("WARNING", logger="apidepth"):
        rl._emit_stale_warnings([42, None, "real-vendor"])
    assert "real-vendor" in caplog.text
    assert "42" not in caplog.text


def test_emit_stale_empty_list_is_noop(caplog):
    with caplog.at_level("WARNING", logger="apidepth"):
        rl._emit_stale_warnings([])
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# _emit_conflict_warnings
# ---------------------------------------------------------------------------


def test_emit_conflict_logs_warning_with_both_hosts(caplog):
    with rl._lock:
        rl._conflict_vendors["mypay"] = {"local": "local.mypay.com", "remote": "api.mypay.com"}
    with caplog.at_level("WARNING", logger="apidepth"):
        rl._emit_conflict_warnings()
    assert "mypay" in caplog.text
    assert "local.mypay.com" in caplog.text
    assert "api.mypay.com" in caplog.text


def test_emit_conflict_clears_conflict_table_after_emit():
    with rl._lock:
        rl._conflict_vendors["vendor-x"] = {"local": "a.com", "remote": "b.com"}
    rl._emit_conflict_warnings()
    with rl._lock:
        assert "vendor-x" not in rl._conflict_vendors


def test_emit_conflict_warns_only_once_per_vendor(caplog):
    with rl._lock:
        rl._conflict_vendors["myapi"] = {"local": "a.com", "remote": "b.com"}
    with caplog.at_level("WARNING", logger="apidepth"):
        rl._emit_conflict_warnings()
        # Simulate next fetch cycle re-detecting the same conflict.
        with rl._lock:
            rl._conflict_vendors["myapi"] = {"local": "a.com", "remote": "b.com"}
        rl._emit_conflict_warnings()
    assert caplog.text.count("myapi") == 1


def test_emit_conflict_noop_when_no_conflicts(caplog):
    with caplog.at_level("WARNING", logger="apidepth"):
        rl._emit_conflict_warnings()
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# _apply_customer_vendors
# ---------------------------------------------------------------------------


def test_apply_customer_vendors_registers_vendor(cfg):
    registry = {"customer_vendors": {"remote-api": "api.remote.example.com"}}
    rl._apply_customer_vendors(registry, cfg)
    assert VendorRegistry.identify("api.remote.example.com", "/v1/resource") is not None


def test_apply_customer_vendors_drops_non_string_key(cfg):
    registry = {"customer_vendors": {42: "dropped.example.com", "kept": "kept.example.com"}}
    rl._apply_customer_vendors(registry, cfg)
    assert VendorRegistry.identify("kept.example.com", "/") is not None
    assert VendorRegistry.identify("dropped.example.com", "/") is None


def test_apply_customer_vendors_drops_non_string_value(cfg):
    registry = {"customer_vendors": {"bad": 999, "ok": "ok.example.com"}}
    rl._apply_customer_vendors(registry, cfg)
    assert VendorRegistry.identify("ok.example.com", "/") is not None


def test_apply_customer_vendors_records_conflict_on_host_mismatch(cfg):
    cfg.extra_vendors = {"shared-api": "local.example.com"}
    registry = {"customer_vendors": {"shared-api": "remote.example.com"}}
    rl._apply_customer_vendors(registry, cfg)
    with rl._lock:
        assert "shared-api" in rl._conflict_vendors
        assert rl._conflict_vendors["shared-api"]["local"] == "local.example.com"
        assert rl._conflict_vendors["shared-api"]["remote"] == "remote.example.com"


def test_apply_customer_vendors_no_conflict_when_hosts_match(cfg):
    cfg.extra_vendors = {"same-api": "same.example.com"}
    registry = {"customer_vendors": {"same-api": "same.example.com"}}
    rl._apply_customer_vendors(registry, cfg)
    with rl._lock:
        assert "same-api" not in rl._conflict_vendors


def test_apply_customer_vendors_noop_when_key_absent(cfg):
    rl._apply_customer_vendors({}, cfg)  # must not raise


def test_apply_customer_vendors_noop_on_non_dict_value(cfg):
    registry = {"customer_vendors": ["not", "a", "dict"]}
    rl._apply_customer_vendors(registry, cfg)  # must not raise


def test_apply_customer_vendors_noop_on_empty_dict(cfg):
    before = VendorRegistry.vendor_count()
    rl._apply_customer_vendors({"customer_vendors": {}}, cfg)
    assert VendorRegistry.vendor_count() == before


# ---------------------------------------------------------------------------
# _emit_warnings
# ---------------------------------------------------------------------------


def test_emit_warnings_logs_stale_vendor(caplog):
    registry = {"warnings": {"stale_vendors": ["openai"]}}
    with caplog.at_level("WARNING", logger="apidepth"):
        rl._emit_warnings(registry)
    assert "openai" in caplog.text


def test_emit_warnings_noop_on_missing_warnings_key():
    rl._emit_warnings({})  # must not raise


def test_emit_warnings_noop_on_non_dict_warnings():
    rl._emit_warnings({"warnings": "not-a-dict"})  # must not raise


def test_emit_warnings_emits_accumulated_conflicts(caplog):
    with rl._lock:
        rl._conflict_vendors["pay-api"] = {"local": "a.com", "remote": "b.com"}
    with caplog.at_level("WARNING", logger="apidepth"):
        rl._emit_warnings({})
    assert "pay-api" in caplog.text


# ---------------------------------------------------------------------------
# _fetch_remote
# ---------------------------------------------------------------------------


def test_fetch_remote_returns_registry_on_200(cfg, minimal_registry):
    body = json.dumps(minimal_registry).encode()
    with patch("http.client.HTTPSConnection", return_value=_mock_conn(200, body)):
        result = rl._fetch_remote(cfg)
    assert result is not None
    assert result["version"] == minimal_registry["version"]


def test_fetch_remote_returns_none_on_404(cfg):
    with patch("http.client.HTTPSConnection", return_value=_mock_conn(404, b"not found")):
        assert rl._fetch_remote(cfg) is None


def test_fetch_remote_returns_none_on_401(cfg):
    with patch("http.client.HTTPSConnection", return_value=_mock_conn(401, b"unauthorized")):
        assert rl._fetch_remote(cfg) is None


def test_fetch_remote_returns_none_on_500(cfg):
    with patch("http.client.HTTPSConnection", return_value=_mock_conn(500, b"")):
        assert rl._fetch_remote(cfg) is None


def test_fetch_remote_returns_none_on_oversized_response(cfg):
    oversized = b"x" * (rl.MAX_RESPONSE_BYTES + 1)
    with patch("http.client.HTTPSConnection", return_value=_mock_conn(200, oversized)):
        assert rl._fetch_remote(cfg) is None


def test_fetch_remote_returns_none_on_invalid_json(cfg):
    with patch("http.client.HTTPSConnection", return_value=_mock_conn(200, b"not json{")):
        assert rl._fetch_remote(cfg) is None


def test_fetch_remote_returns_none_on_network_error(cfg):
    with patch("http.client.HTTPSConnection", side_effect=OSError("connection refused")):
        assert rl._fetch_remote(cfg) is None


def test_fetch_remote_sends_bearer_token(cfg, minimal_registry):
    body = json.dumps(minimal_registry).encode()
    mock_conn = _mock_conn(200, body)
    with patch("http.client.HTTPSConnection", return_value=mock_conn):
        rl._fetch_remote(cfg)
    headers = mock_conn.request.call_args.kwargs["headers"]
    assert headers["Authorization"] == f"Bearer {cfg.api_key}"


def test_fetch_remote_sends_empty_bearer_when_no_api_key(minimal_registry):
    c = Configuration()
    c.api_key = None
    with tempfile.TemporaryDirectory() as tmpdir:
        c.registry_cache_path = os.path.join(tmpdir, "registry.json")
        body = json.dumps(minimal_registry).encode()
        mock_conn = _mock_conn(200, body)
        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            rl._fetch_remote(c)
    headers = mock_conn.request.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer "


def test_fetch_remote_writes_disk_cache_on_success(cfg, minimal_registry):
    body = json.dumps(minimal_registry).encode()
    with patch("http.client.HTTPSConnection", return_value=_mock_conn(200, body)):
        rl._fetch_remote(cfg)
    assert os.path.exists(cfg.registry_cache_path)
    with open(cfg.registry_cache_path, "rb") as f:
        assert json.loads(f.read()) == minimal_registry


def test_fetch_remote_does_not_write_cache_on_non_200(cfg):
    with patch("http.client.HTTPSConnection", return_value=_mock_conn(500, b"")):
        rl._fetch_remote(cfg)
    assert not os.path.exists(cfg.registry_cache_path)


def test_fetch_remote_closes_connection_on_success(cfg, minimal_registry):
    body = json.dumps(minimal_registry).encode()
    mock_conn = _mock_conn(200, body)
    with patch("http.client.HTTPSConnection", return_value=mock_conn):
        rl._fetch_remote(cfg)
    mock_conn.close.assert_called_once()


def test_fetch_remote_closes_connection_on_non_200(cfg):
    mock_conn = _mock_conn(403, b"")
    with patch("http.client.HTTPSConnection", return_value=mock_conn):
        rl._fetch_remote(cfg)
    mock_conn.close.assert_called_once()


def test_fetch_remote_closes_connection_on_network_error(cfg):
    # Connection is created but raises on getresponse — close must still be attempted.
    mock_conn = MagicMock()
    mock_conn.getresponse.side_effect = OSError("reset")
    with patch("http.client.HTTPSConnection", return_value=mock_conn):
        rl._fetch_remote(cfg)
    mock_conn.close.assert_called_once()


def test_fetch_remote_logs_debug_on_non_200(cfg, caplog):
    with patch("http.client.HTTPSConnection", return_value=_mock_conn(403, b"")):
        with caplog.at_level("DEBUG", logger="apidepth"):
            rl._fetch_remote(cfg)
    assert "403" in caplog.text


def test_fetch_remote_logs_debug_on_exception(cfg, caplog):
    with patch("http.client.HTTPSConnection", side_effect=OSError("no route to host")):
        with caplog.at_level("DEBUG", logger="apidepth"):
            rl._fetch_remote(cfg)
    assert "OSError" in caplog.text or "no route" in caplog.text


def test_fetch_remote_logs_warning_on_oversized_response(cfg, caplog):
    oversized = b"x" * (rl.MAX_RESPONSE_BYTES + 1)
    with patch("http.client.HTTPSConnection", return_value=_mock_conn(200, oversized)):
        with caplog.at_level("WARNING", logger="apidepth"):
            rl._fetch_remote(cfg)
    assert "large" in caplog.text.lower() or "bytes" in caplog.text


def test_fetch_remote_applies_customer_vendors_from_payload(cfg):
    registry = {
        "version": "test-1",
        "vendors": [],
        "customer_vendors": {"my-svc": "api.myservice.io"},
    }
    body = json.dumps(registry).encode()
    with patch("http.client.HTTPSConnection", return_value=_mock_conn(200, body)):
        rl._fetch_remote(cfg)
    assert VendorRegistry.identify("api.myservice.io", "/v1/resource") is not None


# ---------------------------------------------------------------------------
# _start_refresh_thread
# ---------------------------------------------------------------------------


def test_start_refresh_thread_returns_started_daemon_thread():
    t = rl._start_refresh_thread()
    assert isinstance(t, threading.Thread)
    assert t.daemon is True
    assert t.name == "apidepth-registry"
    assert t.is_alive()


# ---------------------------------------------------------------------------
# load_and_start
# ---------------------------------------------------------------------------


def test_load_and_start_uses_remote_registry(cfg, minimal_registry):
    with (
        patch("apidepth.registry_loader._fetch_remote", return_value=minimal_registry),
        patch("apidepth.registry_loader._start_refresh_thread"),
        patch("apidepth.collector.Collector.register_fork_safety"),
        patch("apidepth.vendor_registry.VendorRegistry.replace") as mock_replace,
        patch("apidepth.get_configuration", return_value=cfg),
    ):
        rl.load_and_start()
    mock_replace.assert_called_once_with(minimal_registry, cfg.extra_vendors or {})


def test_load_and_start_falls_back_to_disk_when_remote_fails(cfg, minimal_registry):
    with (
        patch("apidepth.registry_loader._fetch_remote", return_value=None),
        patch(
            "apidepth.registry_loader._load_from_disk", return_value=minimal_registry
        ) as mock_disk,
        patch("apidepth.registry_loader._start_refresh_thread"),
        patch("apidepth.collector.Collector.register_fork_safety"),
        patch("apidepth.vendor_registry.VendorRegistry.replace") as mock_replace,
        patch("apidepth.get_configuration", return_value=cfg),
    ):
        rl.load_and_start()
    mock_disk.assert_called_once_with(cfg)
    mock_replace.assert_called_once_with(minimal_registry, cfg.extra_vendors or {})


def test_load_and_start_skips_replace_when_no_registry_available(cfg):
    with (
        patch("apidepth.registry_loader._fetch_remote", return_value=None),
        patch("apidepth.registry_loader._load_from_disk", return_value=None),
        patch("apidepth.registry_loader._start_refresh_thread"),
        patch("apidepth.collector.Collector.register_fork_safety"),
        patch("apidepth.vendor_registry.VendorRegistry.replace") as mock_replace,
        patch("apidepth.get_configuration", return_value=cfg),
    ):
        rl.load_and_start()
    mock_replace.assert_not_called()


def test_load_and_start_always_starts_refresh_thread(cfg):
    with (
        patch("apidepth.registry_loader._fetch_remote", return_value=None),
        patch("apidepth.registry_loader._load_from_disk", return_value=None),
        patch("apidepth.registry_loader._start_refresh_thread") as mock_thread,
        patch("apidepth.collector.Collector.register_fork_safety"),
        patch("apidepth.get_configuration", return_value=cfg),
    ):
        rl.load_and_start()
    mock_thread.assert_called_once()


def test_load_and_start_registers_fork_safety(cfg):
    with (
        patch("apidepth.registry_loader._fetch_remote", return_value=None),
        patch("apidepth.registry_loader._load_from_disk", return_value=None),
        patch("apidepth.registry_loader._start_refresh_thread"),
        patch("apidepth.collector.Collector.register_fork_safety") as mock_fork,
        patch("apidepth.get_configuration", return_value=cfg),
    ):
        rl.load_and_start()
    mock_fork.assert_called_once()
