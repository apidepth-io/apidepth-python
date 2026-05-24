"""SSRF guard tests driven by the canonical fixture in apidepth-collector.

Fixture lives in apidepth-collector/tests/fixtures/private_host_cases.json.
Locally it is resolved relative to this file. In CI it is loaded from a
shallow collector checkout placed at ../apidepth-collector/ (repo root).
See .github/workflows/ci.yml for the checkout step.
"""

import json
import os
from urllib.parse import urlparse

import pytest

from apidepth.collector import _validate_collector_url

_FIXTURE_PATHS = [
    os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "apidepth-collector",
            "tests",
            "fixtures",
            "private_host_cases.json",
        )
    ),
    os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "apidepth-collector",
            "tests",
            "fixtures",
            "private_host_cases.json",
        )
    ),
]


def _load_fixture() -> dict:
    for path in _FIXTURE_PATHS:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    raise FileNotFoundError("private_host_cases.json not found — see tests/test_ssrf.py for setup")


_fixture = _load_fixture()


@pytest.mark.parametrize(
    "case",
    _fixture["must_block"],
    ids=[c["label"] for c in _fixture["must_block"]],
)
def test_blocks_private_host(case: dict) -> None:
    with pytest.raises(ValueError):
        _validate_collector_url(urlparse(f"https://{case['host']}/v1/events"))


@pytest.mark.parametrize(
    "case",
    _fixture["must_allow"],
    ids=[c["label"] for c in _fixture["must_allow"]],
)
def test_allows_public_host(case: dict) -> None:
    _validate_collector_url(urlparse(f"https://{case['host']}/v1/events"))
