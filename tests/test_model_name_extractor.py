"""Tests for apidepth.model_name_extractor.extract()."""

from unittest.mock import MagicMock

from apidepth.model_name_extractor import extract


def _mock_response(
    content_type="application/json", body=b'{"model":"gpt-4-turbo","id":"cmpl-1"}', streaming=False
):
    """Build a minimal mock response object matching requests/httpx shape."""
    resp = MagicMock()
    resp.headers = MagicMock()
    resp.headers.get = lambda key, default="": {
        "content-type": content_type,
    }.get(key, default)

    if streaming:
        # Simulate requests streaming: _content is False (not yet buffered)
        resp._content = False
        resp.content = b""  # shouldn't be reached
    else:
        resp._content = body
        resp.content = body

    return resp


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_extracts_openai_model():
    resp = _mock_response(body=b'{"model":"gpt-4-turbo","choices":[]}')
    assert extract("api.openai.com", resp) == "gpt-4-turbo"


def test_extracts_anthropic_model():
    body = b'{"id":"msg_01","type":"message","model":"claude-3-opus-20240229","content":[]}'
    resp = _mock_response(body=body)
    assert extract("api.anthropic.com", resp) == "claude-3-opus-20240229"


def test_extracts_gemini_model():
    body = b'{"model":"gemini-1.5-flash","candidates":[]}'
    resp = _mock_response(body=body)
    assert extract("generativelanguage.googleapis.com", resp) == "gemini-1.5-flash"


def test_extracts_mistral_model():
    body = b'{"model":"mistral-large-latest","choices":[]}'
    resp = _mock_response(body=body)
    assert extract("api.mistral.ai", resp) == "mistral-large-latest"


def test_extracts_cohere_model():
    body = b'{"model":"command-r-plus","generations":[]}'
    resp = _mock_response(body=body)
    assert extract("api.cohere.com", resp) == "command-r-plus"


# ---------------------------------------------------------------------------
# Non-AI vendor — returns None immediately
# ---------------------------------------------------------------------------


def test_returns_none_for_stripe():
    resp = _mock_response(body=b'{"id":"ch_abc"}')
    assert extract("api.stripe.com", resp) is None


def test_returns_none_for_github():
    resp = _mock_response(body=b'{"login":"user"}')
    assert extract("api.github.com", resp) is None


# ---------------------------------------------------------------------------
# Content-type guard
# ---------------------------------------------------------------------------


def test_returns_none_for_streaming_content_type():
    resp = _mock_response(content_type="text/event-stream", body=b"data: {}\n\n")
    assert extract("api.openai.com", resp) is None


def test_returns_none_for_html_content_type():
    resp = _mock_response(content_type="text/html", body=b"<html/>")
    assert extract("api.openai.com", resp) is None


# ---------------------------------------------------------------------------
# Streaming body guard
# ---------------------------------------------------------------------------


def test_returns_none_when_requests_body_not_buffered():
    """requests streaming responses have _content = False — must not trigger a read."""
    resp = _mock_response(streaming=True)
    assert extract("api.openai.com", resp) is None


def test_returns_none_when_httpx_raises_response_not_read():
    """httpx streaming responses raise on .content — must be silently handled."""
    resp = MagicMock()
    resp.headers = MagicMock()
    resp.headers.get = lambda k, d="": "application/json" if k == "content-type" else d
    resp._content = None  # not False, so falls through to .content
    resp.content = MagicMock(side_effect=Exception("ResponseNotRead"))
    assert extract("api.openai.com", resp) is None


# ---------------------------------------------------------------------------
# Malformed body
# ---------------------------------------------------------------------------


def test_returns_none_for_invalid_json():
    resp = _mock_response(body=b"not-json")
    assert extract("api.openai.com", resp) is None


def test_returns_none_when_model_field_absent():
    resp = _mock_response(body=b'{"choices":[],"usage":{}}')
    assert extract("api.openai.com", resp) is None


def test_returns_none_when_model_is_not_a_string():
    resp = _mock_response(body=b'{"model":42}')
    assert extract("api.openai.com", resp) is None


def test_returns_none_when_model_is_empty_string():
    resp = _mock_response(body=b'{"model":""}')
    assert extract("api.openai.com", resp) is None


def test_returns_none_when_body_is_empty():
    resp = _mock_response(body=b"")
    assert extract("api.openai.com", resp) is None


# ---------------------------------------------------------------------------
# Body truncation
# ---------------------------------------------------------------------------


def test_handles_normal_sized_body():
    # A typical AI API response is well under 8KB — model is found.
    body = b'{"model":"gpt-4o","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":20}}'
    resp = _mock_response(body=body)
    assert extract("api.openai.com", resp) == "gpt-4o"


def test_handles_large_body_with_model_near_start():
    # Large body, model at the start — captured regardless of size (PY-018).
    padding = b" " * 20_000
    body = b'{"model":"gpt-4o",' + padding + b'"choices":[]}'
    resp = _mock_response(body=body)
    assert extract("api.openai.com", resp) == "gpt-4o"


def test_captures_model_after_8kb_boundary():
    # PY-018: embeddings/batch responses put `model` after a large `data` array.
    # The old parse-after-8KB-truncate dropped it; the regex scan now finds it.
    prefix = b'{"object":"list","data":["' + b"x" * 8200
    suffix = b'"],"model":"text-embedding-3-small"}'
    body = prefix + suffix
    assert len(body) > 8192
    resp = _mock_response(body=body)
    assert extract("api.openai.com", resp) == "text-embedding-3-small"


def test_returns_none_when_model_beyond_scan_bound():
    # A model field past the 256KB scan bound is not captured (work is bounded).
    from apidepth.model_name_extractor import _MODEL_SCAN_MAX_BYTES

    body = b'{"data":["' + b"x" * _MODEL_SCAN_MAX_BYTES + b'"],"model":"too-far-away"}'
    resp = _mock_response(body=body)
    assert extract("api.openai.com", resp) is None


# ---------------------------------------------------------------------------
# capture_model_names = False
# ---------------------------------------------------------------------------


def test_returns_none_when_capture_model_names_disabled():
    resp = _mock_response(body=b'{"model":"gpt-4-turbo"}')
    import apidepth

    original = apidepth.get_configuration().capture_model_names
    try:
        apidepth.configure(capture_model_names=False)
        assert extract("api.openai.com", resp) is None
    finally:
        apidepth.configure(capture_model_names=original)
