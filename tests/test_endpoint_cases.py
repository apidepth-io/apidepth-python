"""Endpoint-normalization parity tests driven by the shared golden fixture (XSDK-NORM).

Fixture lives in apidepth-collector/tests/fixtures/endpoint_cases.json and is the
single source of truth every SDK's VendorRegistry.identify must agree with, so the
same host+path normalizes to the same endpoint regardless of language.

Resolution mirrors test_ssrf.py: relative to this file locally, or from a shallow
collector checkout at ../apidepth-collector/ in CI (see .github/workflows/ci.yml).
"""

import json
import os

import pytest

from apidepth.vendor_registry import VendorRegistry

_FIXTURE_PATHS = [
    os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "apidepth-collector",
            "tests",
            "fixtures",
            "endpoint_cases.json",
        )
    ),
    os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "apidepth-collector",
            "tests",
            "fixtures",
            "endpoint_cases.json",
        )
    ),
]


def _load_fixture() -> dict:
    for path in _FIXTURE_PATHS:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    raise FileNotFoundError(
        "endpoint_cases.json not found — see tests/test_endpoint_cases.py for setup"
    )


_fixture = _load_fixture()


@pytest.fixture(autouse=True)
def _reset_registry():
    """Ensure the registry is at the bundled baseline for each case."""
    VendorRegistry.reset()
    yield
    VendorRegistry.reset()


@pytest.mark.parametrize(
    "case",
    _fixture["cases"],
    ids=[c["label"] for c in _fixture["cases"]],
)
def test_endpoint_normalization(case: dict) -> None:
    result = VendorRegistry.identify(case["host"], case["path"])
    assert result is not None, f"{case['host']} not recognised"
    _, endpoint = result
    assert endpoint == case["expected"]
