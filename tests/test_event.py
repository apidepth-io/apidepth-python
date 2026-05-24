"""Tests for apidepth.event.build()."""

import time

import pytest

from apidepth.event import build, REQUIRED


# ---------------------------------------------------------------------------
# build() with all fields present
# ---------------------------------------------------------------------------


def _valid_attrs(**overrides):
    base = {
        "vendor": "stripe",
        "endpoint": "/v1/charges/:id",
        "method": "GET",
        "outcome": "success",
        "duration_ms": 123,
        "ts": int(time.time() * 1000),
    }
    base.update(overrides)
    return base


def test_build_returns_dict_with_all_required_keys():
    attrs = _valid_attrs()
    result = build(attrs)
    for key in REQUIRED:
        assert key in result


def test_build_returns_shallow_copy_not_same_object():
    attrs = _valid_attrs()
    result = build(attrs)
    assert result is not attrs


def test_build_preserves_all_input_values():
    attrs = _valid_attrs(status=200, cold_start=False, env="production")
    result = build(attrs)
    assert result["vendor"] == "stripe"
    assert result["endpoint"] == "/v1/charges/:id"
    assert result["method"] == "GET"
    assert result["outcome"] == "success"
    assert result["duration_ms"] == 123
    assert result["status"] == 200
    assert result["cold_start"] is False
    assert result["env"] == "production"


def test_build_includes_ts_as_integer():
    ts = int(time.time() * 1000)
    result = build(_valid_attrs(ts=ts))
    assert isinstance(result["ts"], int)
    assert result["ts"] == ts


# ---------------------------------------------------------------------------
# Optional fields are passed through
# ---------------------------------------------------------------------------


def test_build_passes_through_optional_status():
    result = build(_valid_attrs(status=429))
    assert result["status"] == 429


def test_build_passes_through_none_status():
    result = build(_valid_attrs(status=None))
    assert result["status"] is None


def test_build_passes_through_error_class():
    result = build(_valid_attrs(error_class="Timeout"))
    assert result["error_class"] == "Timeout"


def test_build_passes_through_rate_limit_fields():
    attrs = _valid_attrs(
        rl_remaining=50,
        rl_limit=100,
        rl_reset_at=1_000_000_090_000,
    )
    result = build(attrs)
    assert result["rl_remaining"] == 50
    assert result["rl_limit"] == 100
    assert result["rl_reset_at"] == 1_000_000_090_000


def test_build_passes_through_cold_start_false():
    result = build(_valid_attrs(cold_start=False))
    assert result["cold_start"] is False


def test_build_passes_through_env():
    result = build(_valid_attrs(env="staging"))
    assert result["env"] == "staging"


# ---------------------------------------------------------------------------
# Missing required fields raise ValueError
# ---------------------------------------------------------------------------


def test_build_raises_for_missing_vendor():
    attrs = _valid_attrs()
    del attrs["vendor"]
    with pytest.raises(ValueError, match="vendor"):
        build(attrs)


def test_build_raises_for_missing_endpoint():
    attrs = _valid_attrs()
    del attrs["endpoint"]
    with pytest.raises(ValueError, match="endpoint"):
        build(attrs)


def test_build_raises_for_missing_method():
    attrs = _valid_attrs()
    del attrs["method"]
    with pytest.raises(ValueError, match="method"):
        build(attrs)


def test_build_raises_for_missing_outcome():
    attrs = _valid_attrs()
    del attrs["outcome"]
    with pytest.raises(ValueError, match="outcome"):
        build(attrs)


def test_build_raises_for_missing_duration_ms():
    attrs = _valid_attrs()
    del attrs["duration_ms"]
    with pytest.raises(ValueError, match="duration_ms"):
        build(attrs)


def test_build_raises_for_missing_ts():
    attrs = _valid_attrs()
    del attrs["ts"]
    with pytest.raises(ValueError, match="ts"):
        build(attrs)


def test_build_error_message_names_all_missing_fields():
    with pytest.raises(ValueError) as exc_info:
        build({"vendor": "stripe"})  # missing endpoint, method, outcome, duration_ms, ts
    msg = str(exc_info.value)
    for field in ("endpoint", "method", "outcome", "duration_ms", "ts"):
        assert field in msg


# ---------------------------------------------------------------------------
# Mutation independence
# ---------------------------------------------------------------------------


def test_build_mutation_of_result_does_not_affect_original():
    attrs = _valid_attrs()
    result = build(attrs)
    result["vendor"] = "mutated"
    assert attrs["vendor"] == "stripe"


def test_build_mutation_of_original_does_not_affect_result():
    attrs = _valid_attrs()
    result = build(attrs)
    attrs["vendor"] = "mutated"
    assert result["vendor"] == "stripe"
