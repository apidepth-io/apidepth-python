"""Tests for apidepth.configure(), get_configuration(), and sanitize_log()."""

import pytest
import apidepth
from apidepth.configuration import Configuration


@pytest.fixture(autouse=True)
def reset_configuration():
    """Reset the global configuration singleton between tests."""
    apidepth._configuration = None
    yield
    apidepth._configuration = None


def test_configure_sets_api_key():
    config = apidepth.configure(api_key="apd_live_test123")
    assert config.api_key == "apd_live_test123"


def test_configure_sets_environment():
    config = apidepth.configure(environment="production")
    assert config.environment == "production"


def test_configure_sets_sample_rate():
    config = apidepth.configure(sample_rate=0.5)
    assert config.sample_rate == 0.5


def test_configure_sets_ignored_hosts():
    hosts = ["internal.example.com", "other.internal"]
    config = apidepth.configure(ignored_hosts=hosts)
    # User hosts are present alongside the hard defaults
    assert frozenset(hosts).issubset(config.ignored_hosts)


def test_configure_sets_multiple_kwargs():
    config = apidepth.configure(api_key="key123", environment="staging", sample_rate=0.25)
    assert config.api_key == "key123"
    assert config.environment == "staging"
    assert config.sample_rate == 0.25


def test_configure_unknown_kwarg_raises_type_error():
    with pytest.raises(TypeError) as exc_info:
        apidepth.configure(nonexistent_option="value")
    assert "nonexistent_option" in str(exc_info.value)


def test_configure_unknown_kwarg_message_contains_key():
    with pytest.raises(TypeError) as exc_info:
        apidepth.configure(bad_key="x", another_bad="y")
    msg = str(exc_info.value)
    assert "bad_key" in msg or "another_bad" in msg


def test_configure_returns_configuration_singleton():
    result = apidepth.configure(api_key="test")
    assert isinstance(result, Configuration)


def test_configure_returns_same_object_as_get_configuration():
    config_from_configure = apidepth.configure(api_key="test")
    config_from_getter = apidepth.get_configuration()
    assert config_from_configure is config_from_getter


def test_get_configuration_creates_default_on_first_call():
    config = apidepth.get_configuration()
    assert isinstance(config, Configuration)
    assert config.enabled is True
    assert config.sample_rate == 1.0
    assert config.api_key is None


def test_get_configuration_is_idempotent():
    first = apidepth.get_configuration()
    second = apidepth.get_configuration()
    assert first is second


def test_get_configuration_returns_same_after_configure():
    apidepth.configure(api_key="abc")
    a = apidepth.get_configuration()
    b = apidepth.get_configuration()
    assert a is b


def test_sanitize_log_strips_carriage_return():
    result = apidepth.sanitize_log("line1\rline2")
    assert "\r" not in result
    assert "line1" in result
    assert "line2" in result


def test_sanitize_log_strips_newline():
    result = apidepth.sanitize_log("line1\nline2")
    assert "\n" not in result


def test_sanitize_log_strips_tab():
    result = apidepth.sanitize_log("col1\tcol2")
    assert "\t" not in result


def test_sanitize_log_strips_all_three():
    result = apidepth.sanitize_log("a\rb\nc\td")
    assert "\r" not in result
    assert "\n" not in result
    assert "\t" not in result


def test_sanitize_log_truncates_to_200_chars():
    long_str = "x" * 300
    result = apidepth.sanitize_log(long_str)
    assert len(result) == 200


def test_sanitize_log_does_not_truncate_short_strings():
    short_str = "hello world"
    result = apidepth.sanitize_log(short_str)
    assert result == short_str


def test_sanitize_log_coerces_non_string():
    result = apidepth.sanitize_log(12345)
    assert result == "12345"


def test_sanitize_log_exactly_200_chars_unchanged():
    s = "a" * 200
    result = apidepth.sanitize_log(s)
    assert len(result) == 200
    assert result == s


# =============================================================================
# ignored_host() — hard defaults and glob matching
# =============================================================================


def test_hard_ignored_hosts_present_by_default():
    config = Configuration()
    for host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):  # nosec B104
        assert config.ignored_host(host), f"{host!r} should be ignored by default"


def test_user_hosts_merged_with_hard_defaults():
    config = Configuration()
    config.ignored_hosts = ["api.internal.example.com"]
    assert config.ignored_host("api.internal.example.com")
    assert config.ignored_host("localhost")


def test_glob_wildcard_ignored_host():
    config = Configuration()
    config.ignored_hosts = ["*.internal", "*.svc.cluster.local"]
    assert config.ignored_host("api.internal")
    assert config.ignored_host("db.internal")
    assert config.ignored_host("service.svc.cluster.local")
    assert not config.ignored_host("api.stripe.com")


def test_collector_url_host_auto_ignored():
    config = Configuration()
    config.collector_url = "https://collector.apidepth.io/v1/events"
    assert config.ignored_host("collector.apidepth.io")


def test_collector_url_updates_ignored_hosts():
    config = Configuration()
    config.collector_url = "https://collector.apidepth.io/v1/events"
    config.collector_url = "https://custom.collector.example.com/v1/events"
    assert config.ignored_host("custom.collector.example.com")


def test_malformed_collector_url_does_not_raise():
    config = Configuration()
    try:
        config.collector_url = "not a url"
    except Exception as exc:
        raise AssertionError(f"Should not raise on malformed URL: {exc}") from exc
