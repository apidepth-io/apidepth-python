"""Tests for VendorRegistry.identify() and load_extra_vendors()."""

import pytest

from apidepth.vendor_registry import VendorRegistry, BUNDLED_BASELINE


@pytest.fixture(autouse=True)
def restore_registry():
    """Restore bundled baseline registry state between tests."""
    from apidepth.vendor_registry import _build_hosts, _build_patterns

    original_hosts = _build_hosts(BUNDLED_BASELINE)
    original_patterns = _build_patterns(BUNDLED_BASELINE)
    original_version = BUNDLED_BASELINE["version"]
    yield
    with VendorRegistry._lock:
        VendorRegistry._hosts = original_hosts
        VendorRegistry._patterns = original_patterns
        VendorRegistry._version = original_version


# ---------------------------------------------------------------------------
# identify() — known vendors
# ---------------------------------------------------------------------------


def test_identify_stripe_charge():
    result = VendorRegistry.identify("api.stripe.com", "/v1/charges/ch_abc123")
    assert result is not None
    vendor, endpoint = result
    assert vendor == "stripe"
    assert endpoint == "/v1/charges/:id"


def test_identify_stripe_customer():
    result = VendorRegistry.identify("api.stripe.com", "/v1/customers/cus_xyz999")
    assert result is not None
    vendor, endpoint = result
    assert vendor == "stripe"
    assert endpoint == "/v1/customers/:id"


def test_identify_openai_chat_completions():
    result = VendorRegistry.identify("api.openai.com", "/v1/chat/completions")
    assert result is not None
    vendor, endpoint = result
    assert vendor == "openai"
    assert endpoint == "/v1/chat/completions"


def test_identify_anthropic_messages():
    result = VendorRegistry.identify("api.anthropic.com", "/v1/messages")
    assert result is not None
    vendor, endpoint = result
    assert vendor == "anthropic"
    assert endpoint == "/v1/messages"


def test_identify_github_repo():
    result = VendorRegistry.identify("api.github.com", "/repos/octocat/hello-world")
    assert result is not None
    vendor, endpoint = result
    assert vendor == "github"
    assert endpoint == "/repos/:owner/:repo"


# ---------------------------------------------------------------------------
# identify() — unknown host
# ---------------------------------------------------------------------------


def test_identify_unknown_host_returns_none():
    result = VendorRegistry.identify("unknown.example.com", "/anything")
    assert result is None


def test_identify_empty_host_returns_none():
    result = VendorRegistry.identify("", "/v1/test")
    assert result is None


# ---------------------------------------------------------------------------
# Generic normalisation (applied after vendor patterns)
# ---------------------------------------------------------------------------


def test_generic_uuid_normalisation():
    result = VendorRegistry.identify(
        "api.stripe.com", "/v1/objects/550e8400-e29b-41d4-a716-446655440000"
    )
    assert result is not None
    _, endpoint = result
    assert "/:uuid" in endpoint


def test_generic_numeric_id_normalisation():
    result = VendorRegistry.identify("api.stripe.com", "/v1/users/12345")
    assert result is not None
    _, endpoint = result
    assert "/:id" in endpoint


def test_generic_token_normalisation_lowercase():
    # 24+ lowercase hex characters → /:token
    result = VendorRegistry.identify("api.stripe.com", "/v1/keys/abcdef1234567890abcdef12")
    assert result is not None
    _, endpoint = result
    assert "/:token" in endpoint


def test_generic_token_normalisation_uppercase():
    # 24+ uppercase hex characters → /:token (bug we fixed: re.IGNORECASE)
    result = VendorRegistry.identify("api.stripe.com", "/v1/keys/ABCDEF1234567890ABCDEF12")
    assert result is not None
    _, endpoint = result
    assert "/:token" in endpoint


def test_query_string_stripped_before_normalisation():
    result = VendorRegistry.identify("api.openai.com", "/v1/chat/completions?stream=true")
    assert result is not None
    _, endpoint = result
    assert "?" not in endpoint


# ---------------------------------------------------------------------------
# load_extra_vendors()
# ---------------------------------------------------------------------------


def test_load_extra_vendors_makes_identify_work():
    VendorRegistry.load_extra_vendors({"my-api": "api.example.com"})
    result = VendorRegistry.identify("api.example.com", "/anything")
    assert result is not None
    vendor, _ = result
    assert vendor == "my-api"


def test_load_extra_vendors_multiple_entries():
    VendorRegistry.load_extra_vendors(
        {
            "payments": "pay.acme.com",
            "notifications": "notify.acme.com",
        }
    )
    assert VendorRegistry.identify("pay.acme.com", "/") is not None
    assert VendorRegistry.identify("notify.acme.com", "/") is not None


def test_load_extra_vendors_empty_dict_is_no_op():
    before_count = VendorRegistry.vendor_count()
    VendorRegistry.load_extra_vendors({})
    after_count = VendorRegistry.vendor_count()
    assert after_count == before_count


def test_load_extra_vendors_none_is_no_op():
    before_count = VendorRegistry.vendor_count()
    VendorRegistry.load_extra_vendors(None)
    after_count = VendorRegistry.vendor_count()
    assert after_count == before_count


def test_load_extra_vendors_unknown_host_still_returns_none_for_other_hosts():
    VendorRegistry.load_extra_vendors({"my-api": "api.example.com"})
    result = VendorRegistry.identify("totally.unregistered.com", "/path")
    assert result is None
