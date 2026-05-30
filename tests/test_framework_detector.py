"""Tests for apidepth.cli.framework_detector."""

import json
import os
import tempfile

import pytest

from apidepth.cli.framework_detector import detect


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------


def test_detects_django(tmpdir):
    _touch(os.path.join(tmpdir, "manage.py"))
    result = detect(directory=tmpdir)
    assert result.name == "django"
    assert result.initializer_path == "settings.py"


def test_detects_fastapi(tmpdir):
    _touch(os.path.join(tmpdir, "app.py"))
    _write(os.path.join(tmpdir, "requirements.txt"), "fastapi==0.110.0\n")
    result = detect(directory=tmpdir)
    assert result.name == "fastapi"


def test_detects_nextjs_js(tmpdir):
    _touch(os.path.join(tmpdir, "next.config.js"))
    result = detect(directory=tmpdir)
    assert result.name == "nextjs"
    assert result.initializer_path == "instrumentation.ts"


def test_detects_nextjs_ts(tmpdir):
    _touch(os.path.join(tmpdir, "next.config.ts"))
    result = detect(directory=tmpdir)
    assert result.name == "nextjs"


def test_detects_express(tmpdir):
    pkg = {"dependencies": {"express": "^4.18.0"}}
    _write(os.path.join(tmpdir, "package.json"), json.dumps(pkg))
    result = detect(directory=tmpdir)
    assert result.name == "express"


def test_falls_back_to_generic(tmpdir):
    result = detect(directory=tmpdir)
    assert result.name == "generic"


def test_django_takes_priority_over_fastapi(tmpdir):
    _touch(os.path.join(tmpdir, "manage.py"))
    _touch(os.path.join(tmpdir, "app.py"))
    _write(os.path.join(tmpdir, "requirements.txt"), "fastapi\n")
    result = detect(directory=tmpdir)
    assert result.name == "django"


# ---------------------------------------------------------------------------
# Snippet content
# ---------------------------------------------------------------------------


def test_injects_api_key_into_snippet(tmpdir):
    result = detect(directory=tmpdir, api_key="apid_live_abc123")
    assert "apid_live_abc123" in result.initializer_snippet


def test_injects_ignored_hosts_into_snippet(tmpdir):
    result = detect(directory=tmpdir, ignored_hosts=["*.internal"])
    assert "*.internal" in result.initializer_snippet


def test_default_api_key_placeholder(tmpdir):
    result = detect(directory=tmpdir)
    assert "YOUR_API_KEY" in result.initializer_snippet
