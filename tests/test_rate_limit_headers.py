"""Tests for rate_limit_headers.extract() and _parse_duration_ms()."""
from apidepth.rate_limit_headers import extract, _parse_duration_ms

NOW_MS = 1_000_000_000_000  # fixed epoch ms for deterministic assertions


# ---------------------------------------------------------------------------
# OpenAI / Anthropic header family
# ---------------------------------------------------------------------------

def test_openai_headers_duration_string_1m30s():
    headers = {
        "x-ratelimit-remaining-requests": "50",
        "x-ratelimit-limit-requests": "100",
        "x-ratelimit-reset-requests": "1m30s",
    }
    result = extract(headers, NOW_MS)
    assert result is not None
    assert result["rl_remaining"] == 50
    assert result["rl_limit"] == 100
    assert result["rl_reset_at"] == NOW_MS + 90_000


def test_openai_headers_reset_0ms_not_none():
    """Reset '0ms' should produce rl_reset_at = now_ms + 0, not None."""
    headers = {
        "x-ratelimit-remaining-requests": "0",
        "x-ratelimit-reset-requests": "0ms",
    }
    result = extract(headers, NOW_MS)
    assert result is not None
    assert "rl_reset_at" in result
    assert result["rl_reset_at"] == NOW_MS + 0


def test_openai_headers_reset_duration_string_20ms():
    headers = {"x-ratelimit-reset-requests": "20ms"}
    result = extract(headers, NOW_MS)
    assert result is not None
    assert result["rl_reset_at"] == NOW_MS + 20


def test_openai_headers_reset_duration_1s():
    headers = {"x-ratelimit-reset-requests": "1s"}
    result = extract(headers, NOW_MS)
    assert result["rl_reset_at"] == NOW_MS + 1000


def test_openai_headers_reset_duration_2h():
    headers = {"x-ratelimit-reset-requests": "2h"}
    result = extract(headers, NOW_MS)
    assert result["rl_reset_at"] == NOW_MS + 7_200_000


# ---------------------------------------------------------------------------
# GitHub header family
# ---------------------------------------------------------------------------

def test_github_headers_unix_timestamp():
    future_unix_ts = NOW_MS // 1000 + 60  # 60 seconds in the future
    headers = {
        "x-ratelimit-remaining": "4999",
        "x-ratelimit-limit": "5000",
        "x-ratelimit-reset": str(future_unix_ts),
    }
    result = extract(headers, NOW_MS)
    assert result is not None
    assert result["rl_remaining"] == 4999
    assert result["rl_limit"] == 5000
    assert result["rl_reset_at"] == future_unix_ts * 1000


def test_github_unix_timestamp_greater_than_now_treated_as_absolute():
    large_ts = 1_716_000_000  # > 1_000_000_000, clearly a Unix ts
    headers = {"x-ratelimit-reset": str(large_ts)}
    result = extract(headers, NOW_MS)
    assert result["rl_reset_at"] == large_ts * 1000


# ---------------------------------------------------------------------------
# IETF draft header family
# ---------------------------------------------------------------------------

def test_ietf_headers_seconds_from_now():
    headers = {
        "ratelimit-remaining": "99",
        "ratelimit-limit": "200",
        "ratelimit-reset": "30",
    }
    result = extract(headers, NOW_MS)
    assert result is not None
    assert result["rl_remaining"] == 99
    assert result["rl_limit"] == 200
    # 30 < 1_000_000_000 so treated as seconds-from-now
    assert result["rl_reset_at"] == NOW_MS + 30_000


# ---------------------------------------------------------------------------
# Stripe retry-after
# ---------------------------------------------------------------------------

def test_stripe_retry_after_integer_seconds():
    headers = {"retry-after": "60"}
    result = extract(headers, NOW_MS)
    assert result is not None
    assert result["rl_reset_at"] == NOW_MS + 60_000


def test_stripe_retry_after_zero_seconds():
    headers = {"retry-after": "0"}
    result = extract(headers, NOW_MS)
    assert result is not None
    assert result["rl_reset_at"] == NOW_MS + 0


# ---------------------------------------------------------------------------
# No headers / partial headers
# ---------------------------------------------------------------------------

def test_no_rate_limit_headers_returns_none():
    headers = {"content-type": "application/json", "x-request-id": "abc123"}
    result = extract(headers, NOW_MS)
    assert result is None


def test_partial_headers_only_remaining_no_limit():
    headers = {"x-ratelimit-remaining-requests": "42"}
    result = extract(headers, NOW_MS)
    assert result is not None
    assert result["rl_remaining"] == 42
    assert "rl_limit" not in result
    assert "rl_reset_at" not in result


def test_partial_headers_only_limit_no_remaining():
    headers = {"x-ratelimit-limit-requests": "1000"}
    result = extract(headers, NOW_MS)
    assert result is not None
    assert result["rl_limit"] == 1000
    assert "rl_remaining" not in result


def test_partial_headers_only_reset():
    headers = {"x-ratelimit-reset-requests": "500ms"}
    result = extract(headers, NOW_MS)
    assert result is not None
    assert result["rl_reset_at"] == NOW_MS + 500
    assert "rl_remaining" not in result
    assert "rl_limit" not in result


# ---------------------------------------------------------------------------
# _parse_duration_ms
# ---------------------------------------------------------------------------

def test_parse_duration_ms_1m30s():
    assert _parse_duration_ms("1m30s") == 90_000


def test_parse_duration_ms_2h():
    assert _parse_duration_ms("2h") == 7_200_000


def test_parse_duration_ms_500ms():
    assert _parse_duration_ms("500ms") == 500


def test_parse_duration_ms_0ms():
    assert _parse_duration_ms("0ms") == 0


def test_parse_duration_ms_1s():
    assert _parse_duration_ms("1s") == 1000


def test_parse_duration_ms_unparseable_returns_none():
    assert _parse_duration_ms("not-a-duration") is None


def test_parse_duration_ms_empty_string_returns_none():
    assert _parse_duration_ms("") is None


def test_parse_duration_ms_combined_1h30m():
    assert _parse_duration_ms("1h30m") == (60 + 30) * 60_000


def test_parse_duration_ms_20ms():
    assert _parse_duration_ms("20ms") == 20
